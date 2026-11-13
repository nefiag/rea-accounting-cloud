# -*- coding: utf-8 -*-
import io
import re
import hashlib
import calendar
from decimal import Decimal
from datetime import datetime, date
from typing import List, Dict, Any, Tuple
import pandas as pd
import pdfplumber
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ChartOfAccount, BankAccount, BankStatement

OPENING_CUTOFF = date(2024, 1, 1)

BANK_TAIL_TO_CODE = {
    "7103": "100209",   # 农商行城北支行7103
    "6321": "100215",   # 农商行6321
    "1502": "100211",   # 宁波银行1502
    "1446": "100212",   # 宁波银行1446
    "7104": "100208",   # 农商行监管账户7104
    "10047": "100210",  # 农商行贷款专户10047
}

LOAN_TAIL_TO_CODE = {
    "67859": "230102",   # 长期借款_银行贷款（农商行城北支行）
    "077006": "230103",  # 长期借款_银行贷款（宁波银行）
}

# 仅针对【摘要 + 附言】匹配的业务关键词（绝不扫描对方户名，防止“汽车维修服务有限公司”被误判为维修费）
SUMMARY_KEYWORD_RULES = [
    (["工资", "年终奖", "代发工资"], "2151", "SALARY_PAYOUT", Decimal("0.9800"), False),
    (["入息", "结息", "利息收入"], "550302", "BANK_INTEREST_RED", Decimal("0.9900"), True),
    (["手续费", "短信收费", "网银服务费", "账户管理费", "年费", "工本费"], "550301", "BANK_FEE", Decimal("0.9900"), False),
    (["归还利息", "贷款利息", "利息支出"], "550303", "LOAN_INTEREST", Decimal("0.9800"), False),
    (["归还贷款", "还本金", "贷款本金", "贷款批扣"], "230102", "LOAN_PRINCIPAL", Decimal("0.9800"), False),
    (["公积金", "住房公积金"], "550212", "HOUSING_FUND", Decimal("0.9800"), False),
    (["社保", "社会保险"], "550213", "SOCIAL_SECURITY", Decimal("0.9800"), False),
    (["税费扣缴", "暂收款", "税务"], "217115", "TAX_WITHHOLDING", Decimal("0.9700"), False),
    (["光伏发电", "分布式光伏"], "510105", "SOLAR_REVENUE", Decimal("0.9800"), False),
    (["停车费", "神州路通"], "510203", "PARKING_REVENUE", Decimal("0.9800"), False),
    (["维保费", "修缮", "装修", "门窗", "维修费"], "550220", "MAINTENANCE_EXP", Decimal("0.9300"), False),
    (["垃圾清运"], "540106", "GARBAGE_COST", Decimal("0.9600"), False),
    (["财产保险", "车险", "保费"], "550210", "INSURANCE_EXP", Decimal("0.9600"), False),
    (["代理记账", "办公费", "检测费", "刻字"], "550203", "OFFICE_EXP", Decimal("0.9300"), False),
    (["差旅", "机票", "高铁", "住宿"], "550204", "TRAVEL_EXP", Decimal("0.9200"), False),
    (["电费", "供电"], "113213", "PREPAID_POWER", Decimal("0.9500"), False),
    (["水费", "自来水"], "113214", "PREPAID_WATER", Decimal("0.9500"), False),
]


def _parse_date(val: Any) -> date | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()[:10].replace("/", "-").replace(".", "-")
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _month_end_dt(d: date) -> datetime:
    last_day = calendar.monthrange(d.year, d.month)[1]
    return datetime(d.year, d.month, last_day, 12, 0, 0)


def _detect_account_code(filename: str, text_hint: str, fallback_code: str) -> str:
    combined = f"{filename} {text_hint}"
    for tail, code in BANK_TAIL_TO_CODE.items():
        if tail in combined:
            return code
    return fallback_code


def _extract_rows(file_bytes: bytes, filename: str, fallback_code: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    lower = filename.lower()

    if lower.endswith((".xlsx", ".xls", ".csv")):
        df_raw = pd.read_csv(io.BytesIO(file_bytes), header=None) if lower.endswith(".csv") else pd.read_excel(io.BytesIO(file_bytes), header=None)
        # 自动探测真实表头行（兼容网银顶部带有账户信息汇总行的报表）
        header_idx = 0
        for i in range(min(15, len(df_raw))):
            row_vals = [str(v).strip() for v in df_raw.iloc[i].tolist()]
            if any(k in row_vals for k in ["交易日期", "日期", "交易时间", "记账日期"]):
                header_idx = i
                break

        top_meta = " ".join(str(x) for x in df_raw.iloc[:header_idx].values.flatten() if pd.notna(x))
        df = df_raw.iloc[header_idx + 1:].copy()
        df.columns = [str(c).strip() for c in df_raw.iloc[header_idx].tolist()]
        file_bank_code = _detect_account_code(filename, f"{top_meta} {' '.join(df.columns)}", fallback_code)

        for idx, r in df.iterrows():
            tx_d = _parse_date(r.get("交易日期") or r.get("日期") or r.get("交易时间") or r.get("记账日期"))
            if not tx_d or tx_d < OPENING_CUTOFF:
                continue

            # 兼容“收入/支出”、“借方发生额/贷方发生额”或单列“发生额+借贷标志”
            in_val = float(pd.to_numeric(r.get("收入") or r.get("收入金额") or r.get("贷方") or r.get("贷方金额") or r.get("贷方发生额") or 0, errors="coerce") or 0)
            out_val = float(pd.to_numeric(r.get("支出") or r.get("支出金额") or r.get("借方") or r.get("借方金额") or r.get("借方发生额") or 0, errors="coerce") or 0)
            single_amt = float(pd.to_numeric(r.get("交易金额") or r.get("发生额") or r.get("金额") or 0, errors="coerce") or 0)
            dc_flag = str(r.get("借贷标志") or r.get("收支方向") or r.get("借/贷") or "").strip()
            bal = float(pd.to_numeric(r.get("账户余额") or r.get("余额") or r.get("可用余额") or 0, errors="coerce") or 0)

            cp_name = str(r.get("对方户名") or r.get("交易对手") or r.get("对方单位名称") or "").strip()
            cp_acct = str(r.get("对方账号") or r.get("对方账户") or "").strip()
            summary = str(r.get("摘要") or r.get("交易类型") or "").strip()
            remark = str(r.get("用途/附言/备注") or r.get("用途") or r.get("附言") or r.get("备注") or "").strip()
            raw_sum = f"{summary} {remark}".strip()

            if abs(in_val) > 1e-6:
                direction, amt = "IN", in_val
            elif abs(out_val) > 1e-6:
                direction, amt = "OUT", out_val
            elif abs(single_amt) > 1e-6:
                if dc_flag in ("贷", "收", "收入", "CR", "C") or "转入" in raw_sum:
                    direction, amt = "IN", abs(single_amt)
                elif dc_flag in ("借", "支", "支出", "DR", "D") or "划出" in raw_sum:
                    direction, amt = "OUT", abs(single_amt)
                else:
                    direction, amt = ("IN", single_amt) if single_amt > 0 else ("OUT", abs(single_amt))
            else:
                continue

            # 二次语义纠偏：银行摘要明确注明“他行转入”且非冲正时，强制为 IN
            if "他行转入" in raw_sum and "冲正" not in raw_sum:
                direction = "IN"
            elif "他行划出" in raw_sum and "冲正" not in raw_sum:
                direction = "OUT"

            row_acct_hint = str(r.get("本方账号") or r.get("账号") or "").strip()
            bank_code = _detect_account_code(filename, row_acct_hint, file_bank_code)

            rows.append({
                "source_file": filename,
                "row_idx": idx,
                "bank_code": bank_code,
                "tx_date": tx_d,
                "direction": direction,
                "amount": round(amt, 2),
                "balance_after": round(bal, 2),
                "counterparty_name": "" if cp_name in ("nan", "None", "--") else cp_name,
                "counterparty_account": "" if cp_acct in ("nan", "None", "--") else cp_acct,
                "raw_summary": raw_sum,
            })

    elif lower.endswith(".pdf"):
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            head_text = "\n".join(p.extract_text() or "" for p in pdf.pages[:2])
            file_bank_code = _detect_account_code(filename, head_text[:800], fallback_code)
            for p_idx, page in enumerate(pdf.pages):
                for t_idx, tb in enumerate(page.extract_tables() or []):
                    for r_idx, line in enumerate(tb):
                        cells = [str(c or "").replace("\n", "").strip() for c in line]
                        if len(cells) < 5:
                            continue
                        tx_d = None
                        for c in cells[:3]:
                            tx_d = _parse_date(c)
                            if tx_d:
                                break
                        if not tx_d or tx_d < OPENING_CUTOFF:
                            continue
                        nums = []
                        for c in cells[2:7]:
                            cleaned = c.replace(",", "").replace("¥", "")
                            if re.match(r"^-?\d+(\.\d+)?$", cleaned):
                                nums.append(float(cleaned))
                        if not nums:
                            continue
                        joined = " ".join(cells)
                        direction = "IN" if any(k in joined for k in ["贷", "收入", "转入", "入息", "房租", "光伏", "停车"]) else "OUT"
                        rows.append({
                            "source_file": filename,
                            "row_idx": p_idx * 10000 + t_idx * 100 + r_idx,
                            "bank_code": file_bank_code,
                            "tx_date": tx_d,
                            "direction": direction,
                            "amount": round(nums[0], 2),
                            "balance_after": round(nums[-1], 2) if len(nums) >= 2 else None,
                            "counterparty_name": cells[6] if len(cells) > 6 else "",
                            "counterparty_account": cells[5] if len(cells) > 5 else "",
                            "raw_summary": " ".join(cells[7:]) if len(cells) > 7 else joined[:80],
                        })
    return rows


async def ingest_bank_statement_batch(
    db: AsyncSession,
    account_set_id: str,
    default_bank_account_id: str,
    files: List[Tuple[str, bytes]],
) -> Dict[str, Any]:
    bank_accs = (
        await db.execute(select(BankAccount).where(BankAccount.account_set_id == account_set_id))
    ).scalars().all()
    code_to_bank_id = {b.linked_account_code: b.id for b in bank_accs}
    fallback_bank = next((b for b in bank_accs if b.id == default_bank_account_id), None)
    fallback_code = fallback_bank.linked_account_code if fallback_bank else "100209"

    all_raw_rows: List[Dict[str, Any]] = []
    for fname, fbytes in files:
        try:
            all_raw_rows.extend(_extract_rows(fbytes, fname, fallback_code))
        except Exception as e:
            raise ValueError(f"文件 [{fname}] 解析异常：{str(e)}")

    total_extracted = len(all_raw_rows)
    if not all_raw_rows:
        return {"statements": [], "total_extracted": 0, "dedup_skipped": 0}

    coa_list = (
        await db.execute(
            select(ChartOfAccount).where(ChartOfAccount.account_set_id == account_set_id)
        )
    ).scalars().all()

    name_to_ar: Dict[str, str] = {}
    name_to_ap: Dict[str, str] = {}
    name_to_other: Dict[str, str] = {}
    for c in coa_list:
        tail_name = c.name.split("_")[-1].strip()
        if c.code.startswith("1131_"):
            name_to_ar[tail_name] = c.code
        elif c.code.startswith("2121_"):
            name_to_ap[tail_name] = c.code
        elif c.code.startswith("2181") and len(c.code) >= 6:
            name_to_other[tail_name] = c.code

    outgoing_internal = set()
    for r in all_raw_rows:
        if "博士特" in r["counterparty_name"] and r["direction"] == "OUT":
            for tail, t_code in BANK_TAIL_TO_CODE.items():
                if tail in r["counterparty_account"] and t_code != r["bank_code"]:
                    outgoing_internal.add((r["tx_date"], round(abs(r["amount"]), 2), r["bank_code"], t_code))

    seen_batch_fps = set()
    dedup_skipped = 0
    created_records: List[BankStatement] = []

    for r in all_raw_rows:
        tx_d: date = r["tx_date"]
        cp = r["counterparty_name"]
        cp_acct = r["counterparty_account"]
        direction = r["direction"]
        amt = r["amount"]
        current_bank_code = r["bank_code"]
        target_bank_account_id = code_to_bank_id.get(current_bank_code, default_bank_account_id)
        raw_sum = r["raw_summary"]
        text_all = f"{cp} {raw_sum}"

        # 去重 1：跨文件自有账户互转的收款重复行
        if "博士特" in cp and direction == "IN":
            skip_dup = False
            for tail, s_code in BANK_TAIL_TO_CODE.items():
                if tail in cp_acct and (tx_d, round(abs(amt), 2), s_code, current_bank_code) in outgoing_internal:
                    skip_dup = True
                    break
            if skip_dup:
                dedup_skipped += 1
                continue

        # 去重 2：跨文件/重传文件相同流水指纹
        fp_raw = f"{account_set_id}|{current_bank_code}|{tx_d.isoformat()}|{direction}|{amt:.2f}|{cp}|{cp_acct}|{raw_sum}"
        fp = hashlib.sha256(fp_raw.encode("utf-8")).hexdigest()
        if fp in seen_batch_fps:
            dedup_skipped += 1
            continue
        seen_batch_fps.add(fp)

        exists = (
            await db.execute(select(BankStatement).where(BankStatement.tx_fingerprint == fp))
        ).scalar_one_or_none()
        if exists:
            dedup_skipped += 1
            continue

        suggested_code = ""
        biz_tag = "GENERAL"
        conf = Decimal("0.8800")
        is_red = amt < 0
        label = raw_sum or ("收款" if direction == "IN" else "付款")

        # 规则 1：自有账户内部划转
        if "博士特" in cp and direction == "OUT":
            target_code = None
            for tail, t_code in BANK_TAIL_TO_CODE.items():
                if tail in cp_acct and t_code != current_bank_code:
                    target_code = t_code
                    break
            if target_code:
                suggested_code = target_code
                biz_tag = "INTERNAL_TRANSFER"
                conf = Decimal("0.9900")
                label = "往来款"
            elif current_bank_code == "100215" or "支取" in text_all:
                suggested_code = "1001"
                biz_tag = "CASH_WITHDRAWAL"
                conf = Decimal("0.9800")
                label = "支取"

        # 规则 2：跨公司关联方（昆山宇天 -> 113340）
        if not suggested_code and ("昆山宇天" in cp or "宇天新能源" in cp):
            suggested_code = "113340"
            biz_tag = "INTERCOMPANY_YT"
            conf = Decimal("0.9800")

        # 规则 3：特殊贷款归集账号
        if not suggested_code:
            for l_tail, l_code in LOAN_TAIL_TO_CODE.items():
                if l_tail in cp_acct:
                    suggested_code = "550303" if "息" in raw_sum else l_code
                    biz_tag = "LOAN_INTEREST" if "息" in raw_sum else "LOAN_PRINCIPAL"
                    conf = Decimal("0.9800")
                    break

        # 规则 4：摘要专属业务关键词（优先于往来户名，防止“工资：黄旭”被误归为股东往来）
        if not suggested_code:
            for kws, code, tag, c_score, red_flag in SUMMARY_KEYWORD_RULES:
                if any(k in raw_sum for k in kws):
                    suggested_code = code
                    biz_tag = tag
                    conf = c_score
                    if red_flag:
                        is_red = True
                    break

        # 规则 5：股东/高管往来（非工资场景下的黄旭）
        if not suggested_code and ("黄旭" in cp or "WONGKING" in cp or "WONG KING" in cp):
            suggested_code = "218108"
            biz_tag = "SHAREHOLDER_CURRENT"
            conf = Decimal("0.9800")

        # 规则 6：往来单位档案精确匹配（客户 1131 / 供应商 2121 / 其他往来 2181）
        if not suggested_code and cp:
            if direction == "IN":
                suggested_code = name_to_ar.get(cp) or name_to_other.get(cp) or "1131_1131036"
                biz_tag = "CUSTOMER_RECEIPT"
            else:
                suggested_code = name_to_ap.get(cp) or name_to_other.get(cp) or "2121_011"
                biz_tag = "VENDOR_PAYMENT"
            conf = Decimal("0.9500") if suggested_code not in ("1131_1131036", "2121_011") else Decimal("0.7500")

        if not suggested_code:
            suggested_code = "1131_1131036" if direction == "IN" else "2121_011"
            conf = Decimal("0.7000")

        mmdd = tx_d.strftime("%m/%d")
        norm_summary = label if label.startswith(mmdd) else f"{mmdd}{label}"
        if cp and cp not in norm_summary:
            norm_summary = f"{norm_summary}：{cp}"

        stmt = BankStatement(
            account_set_id=account_set_id,
            bank_account_id=target_bank_account_id,
            tx_fingerprint=fp,
            tx_date=_month_end_dt(tx_d),
            direction=direction,
            amount=Decimal(str(-abs(amt) if is_red else amt)),
            balance_after=Decimal(str(r["balance_after"])) if r["balance_after"] is not None else None,
            counterparty_name=cp,
            counterparty_account=cp_acct,
            raw_summary=raw_sum,
            ai_tags={
                "biz_tag": biz_tag,
                "is_red_ink": is_red,
                "actual_tx_date": tx_d.isoformat(),
                "normalized_summary": norm_summary,
                "source_file": r["source_file"],
            },
            suggested_account_code=suggested_code,
            confidence_score=conf,
            status="PARSED",
        )
        db.add(stmt)
        created_records.append(stmt)

    await db.commit()
    for s in created_records:
        await db.refresh(s)

    return {
        "statements": created_records,
        "total_extracted": total_extracted,
        "dedup_skipped": dedup_skipped,
    }


async def ingest_bank_statement_file(
    db: AsyncSession,
    account_set_id: str,
    bank_account_id: str,
    filename: str,
    content: bytes,
) -> List[BankStatement]:
    res = await ingest_bank_statement_batch(
        db=db,
        account_set_id=account_set_id,
        default_bank_account_id=bank_account_id,
        files=[(filename, content)],
    )
    return res["statements"]
