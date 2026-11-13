import hashlib
import io
import json
import os
from datetime import datetime
from decimal import Decimal
import pandas as pd
import pdfplumber
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..models import BankStatement, ChartOfAccount, ReaAgent


def _make_fingerprint(bank_account_id: str, tx_date: str, serial: str, amount: str, idx: int) -> str:
    raw = f"{bank_account_id}|{tx_date}|{serial}|{amount}|{idx}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_raw_file(filename: str, content: bytes) -> list[dict]:
    ext = filename.lower().split(".")[-1]
    records = []

    if ext in ("xlsx", "xls", "csv"):
        df = pd.read_csv(io.BytesIO(content)) if ext == "csv" else pd.read_excel(io.BytesIO(content))
        header_idx = None
        for i, row in df.iterrows():
            row_str = " ".join(str(v) for v in row.values if pd.notna(v))
            if any(k in row_str for k in ("交易时间", "交易日期", "记账日期", "发生额", "收入", "支出", "借方", "贷方")):
                header_idx = i
                break
        if header_idx is not None:
            df.columns = df.iloc[header_idx]
            df = df.iloc[header_idx + 1 :].reset_index(drop=True)

        df = df.dropna(how="all")
        for idx, row in df.iterrows():
            row_dict = {str(k).strip(): ("" if pd.isna(v) else str(v).strip()) for k, v in row.items()}
            joined = "".join(row_dict.values())
            if any(skip in joined for skip in ("合计", "总计", "期初余额", "本页小计")):
                continue
            records.append(row_dict)

    elif ext == "pdf":
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for tbl in tables:
                    if len(tbl) < 2:
                        continue
                    headers = [str(h or f"col_{i}").strip() for i, h in enumerate(tbl[0])]
                    for r in tbl[1:]:
                        records.append({headers[i]: str(r[i] or "").strip() for i in range(min(len(headers), len(r)))})
    return records


async def _classify_with_llm_or_rules(
    raw_rows: list[dict], accounts: list[ChartOfAccount], agents: list[ReaAgent]
) -> list[dict]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    account_catalog = [{"code": a.code, "name": a.name} for a in accounts if a.is_leaf]
    agent_names = [ag.name for ag in agents]

    if api_key and api_key != "sk-your-api-key":
        try:
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            )
            prompt = (
                "你是资深中国注册会计师与REA财务AI助手。请将以下银行对账单原始行标准化，"
                "提取交易时间(tx_date, YYYY-MM-DD HH:MM:SS)、资金方向(direction: IN代表收款流入, OUT代表付款流出)、"
                "正数金额(amount)、交易后余额(balance_after, 可空)、对方户名(counterparty_name)、"
                "对方账号(counterparty_account)、银行流水号(bank_serial_no)、原始摘要(raw_summary)，"
                "并打上结构化会计标签: intent_category(CUSTOMER_PAYMENT/VENDOR_PAYMENT/BANK_FEE/TAX_PAYMENT/SALARY/INTERNAL_TRANSFER/OTHER)、"
                "normalized_summary(规范凭证摘要)、suggested_account_code(从给定末级科目表中选最匹配编码)、confidence_score(0~1)。\n"
                f"可用末级会计科目表: {json.dumps(account_catalog, ensure_ascii=False)}\n"
                f"已有往来单位名录: {json.dumps(agent_names, ensure_ascii=False)}\n"
                f"待解析流水数据: {json.dumps(raw_rows, ensure_ascii=False)}\n"
                "严格返回JSON对象: {\"items\": [...]}"
            )
            resp = await client.chat.completions.create(
                model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            parsed = json.loads(resp.choices[0].message.content)
            if "items" in parsed and isinstance(parsed["items"], list):
                return parsed["items"]
        except Exception as e:
            print(f"[StatementAgent] LLM调用失败，降级使用内置规则引擎: {e}")

    results = []
    for r in raw_rows:
        tx_date = ""
        amount = Decimal("0.00")
        direction = "IN"
        balance_after = None
        counterparty = ""
        counter_acc = ""
        serial = ""
        summary = ""

        for k, v in r.items():
            if any(t in k for t in ("日期", "时间", "Date")) and not tx_date and v:
                tx_date = v.replace("/", "-")
            elif any(t in k for t in ("流水", "凭证号", "回单", "Serial")) and v:
                serial = v
            elif any(t in k for t in ("对方户名", "对方名称", "交易对手", "Counterparty")) and v:
                counterparty = v
            elif any(t in k for t in ("对方账号", "对手账号")) and v:
                counter_acc = v
            elif any(t in k for t in ("摘要", "用途", "附言", "备注", "Summary")) and v:
                summary = f"{summary} {v}".strip()
            elif "余额" in k and v:
                try:
                    balance_after = str(Decimal(v.replace(",", "")))
                except Exception:
                    pass
            elif any(t in k for t in ("收入", "贷方", "转入", "Credit")) and v:
                try:
                    val = Decimal(v.replace(",", ""))
                    if val > 0:
                        amount = val
                        direction = "IN"
                except Exception:
                    pass
            elif any(t in k for t in ("支出", "借方", "转出", "Debit")) and v:
                try:
                    val = Decimal(v.replace(",", ""))
                    if val > 0:
                        amount = val
                        direction = "OUT"
                except Exception:
                    pass
            elif "金额" in k and amount == Decimal("0.00") and v:
                try:
                    val = Decimal(v.replace(",", ""))
                    direction = "IN" if val >= 0 else "OUT"
                    amount = abs(val)
                except Exception:
                    pass

        if amount == Decimal("0.00"):
            continue

        if not tx_date:
            tx_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        elif len(tx_date) == 10:
            tx_date = f"{tx_date} 12:00:00"

        text_all = f"{counterparty} {summary}"
        if any(w in text_all for w in ("手续费", "工本费", "短信费", "账户管理费", "结息")):
            if "结息" in text_all or "利息" in text_all:
                intent, acc_code, norm_sum = "BANK_INTEREST", "660302", "银行存款利息收入"
            else:
                intent, acc_code, norm_sum = "BANK_FEE", "660301", f"支付银行手续费({summary or '网银扣费'})"
            conf = 0.96
        elif any(w in text_all for w in ("税", "税务", "社保", "公积金")):
            intent, acc_code, norm_sum = "TAX_PAYMENT", "2221", f"缴纳税费/社保({summary})"
            conf = 0.91
        elif any(w in text_all for w in ("工资", "薪资", "代发")):
            intent, acc_code, norm_sum = "SALARY", "2211", "发放员工工资"
            conf = 0.93
        elif direction == "IN":
            intent, acc_code, norm_sum = "CUSTOMER_PAYMENT", "1122", f"收到{counterparty or '客户'}货款"
            conf = 0.88
        else:
            intent, acc_code, norm_sum = "VENDOR_PAYMENT", "2202", f"支付{counterparty or '供应商'}款项"
            conf = 0.86

        results.append(
            {
                "tx_date": tx_date,
                "direction": direction,
                "amount": str(amount),
                "balance_after": balance_after,
                "counterparty_name": counterparty,
                "counterparty_account": counter_acc,
                "bank_serial_no": serial,
                "raw_summary": summary,
                "intent_category": intent,
                "normalized_summary": norm_sum,
                "suggested_account_code": acc_code,
                "confidence_score": conf,
            }
        )
    return results


async def ingest_bank_statement_file(
    db: AsyncSession,
    account_set_id: str,
    bank_account_id: str,
    filename: str,
    content: bytes,
) -> list[BankStatement]:
    raw_rows = parse_raw_file(filename, content)
    acc_res = await db.execute(
        select(ChartOfAccount).where(ChartOfAccount.account_set_id == account_set_id)
    )
    accounts = acc_res.scalars().all()

    ag_res = await db.execute(
        select(ReaAgent).where(ReaAgent.account_set_id == account_set_id)
    )
    agents = ag_res.scalars().all()
    agent_by_name = {ag.name: ag for ag in agents}

    classified = await _classify_with_llm_or_rules(raw_rows, accounts, agents)
    saved_statements: list[BankStatement] = []

    for idx, item in enumerate(classified):
        fp = _make_fingerprint(
            bank_account_id,
            item["tx_date"],
            item.get("bank_serial_no", ""),
            str(item["amount"]),
            idx,
        )
        exist = await db.execute(select(BankStatement).where(BankStatement.tx_fingerprint == fp))
        if exist.scalar_one_or_none():
            continue

        cp_name = (item.get("counterparty_name") or "").strip()
        matched_agent_id = None
        if cp_name:
            if cp_name in agent_by_name:
                matched_agent_id = agent_by_name[cp_name].id
            else:
                ag_type = "CUSTOMER" if item["direction"] == "IN" else "VENDOR"
                new_ag = ReaAgent(
                    account_set_id=account_set_id,
                    agent_type=ag_type,
                    code=f"AG{len(agent_by_name)+1:04d}",
                    name=cp_name,
                )
                db.add(new_ag)
                await db.flush()
                agent_by_name[cp_name] = new_ag
                matched_agent_id = new_ag.id

        try:
            dt = datetime.fromisoformat(item["tx_date"])
        except Exception:
            dt = datetime.now()

        bal_after = Decimal(str(item["balance_after"])) if item.get("balance_after") else None

        stmt = BankStatement(
            account_set_id=account_set_id,
            bank_account_id=bank_account_id,
            tx_fingerprint=fp,
            tx_date=dt,
            bank_serial_no=item.get("bank_serial_no", ""),
            direction=item["direction"],
            amount=Decimal(str(item["amount"])),
            balance_after=bal_after,
            counterparty_name=cp_name,
            counterparty_account=item.get("counterparty_account", ""),
            raw_summary=item.get("raw_summary", ""),
            ai_tags={
                "intent_category": item.get("intent_category", "OTHER"),
                "normalized_summary": item.get("normalized_summary", ""),
                "counterparty_name": cp_name,
            },
            matched_agent_id=matched_agent_id,
            suggested_account_code=item.get("suggested_account_code", "1122"),
            confidence_score=Decimal(str(item.get("confidence_score", 0.85))),
            status="PARSED",
        )
        db.add(stmt)
        saved_statements.append(stmt)

    await db.commit()
    for s in saved_statements:
        await db.refresh(s)
    return saved_statements