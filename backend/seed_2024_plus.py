# -*- coding: utf-8 -*-
"""
全量灌入博士特 2024-01-01 期初余额与 2024-01 ~ 2026-08 全部记账凭证（仅处理 2024+ 账目）
"""
import os
import re
import sys
import asyncio
from decimal import Decimal
from datetime import datetime, date
import pandas as pd
from sqlalchemy import delete

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.database import engine, Base, AsyncSessionLocal
from app.models import (
    AccountSet,
    ChartOfAccount,
    BankAccount,
    BankStatement,
    Voucher,
    VoucherEntry,
    AccountBalance,
)

DEFAULT_ACCOUNT_SET_ID = "00000000-0000-0000-0000-000000000001"
SEED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_data")
CSV_PATH = os.path.join(SEED_DIR, "科目表_博士特.csv")
XLSX_PATH = os.path.join(SEED_DIR, "明细账_博士特_2024-2026.08.xlsx")
OPENING_CUTOFF = date(2024, 1, 1)


def infer_cat_dir(code: str) -> tuple[str, str]:
    if code.startswith("1"):
        return "资产", "DEBIT"
    if code.startswith("2"):
        return "负债", "CREDIT"
    if code.startswith("3"):
        return "权益", "CREDIT"
    return "损益", "CREDIT" if code.startswith(("510", "530")) else "DEBIT"


async def run_seed():
    if not os.path.exists(CSV_PATH) or not os.path.exists(XLSX_PATH):
        print(f"❌ 请先将以下两个文件放入 {SEED_DIR} 目录：")
        print("   1. 科目表_博士特.csv")
        print("   2. 明细账_博士特_2024-2026.08.xlsx")
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 1. 读取 2023-12 期末余额作为 2024-01-01 期初
    df_csv = pd.read_csv(CSV_PATH)
    subjects: dict[str, dict] = {}
    for _, r in df_csv.iterrows():
        raw = str(r["科目"]).strip()
        if "-" not in raw:
            continue
        code, name = [x.strip() for x in raw.split("-", 1)]
        bal = float(r["末余额"]) if pd.notna(r["末余额"]) else 0.0
        d_str = str(r["末余额方向"]).strip() if pd.notna(r["末余额方向"]) else "平"
        cat, norm_dir = infer_cat_dir(code)
        if (d_str == "借" and norm_dir == "CREDIT") or (d_str == "贷" and norm_dir == "DEBIT"):
            opening = -bal
        else:
            opening = bal
        subjects[code] = {
            "code": code,
            "name": name,
            "category": cat,
            "balance_direction": norm_dir,
            "opening": round(opening, 2),
        }

    # 2. 补充 2024-2026.08 新增科目
    df_ledger = pd.read_excel(XLSX_PATH, sheet_name="明细账博士特", header=3)
    for raw in df_ledger["科目"].dropna().unique():
        raw = str(raw).strip()
        if "-" not in raw:
            continue
        code, name = [x.strip() for x in raw.split("-", 1)]
        if code not in subjects:
            cat, norm_dir = infer_cat_dir(code)
            subjects[code] = {
                "code": code,
                "name": name,
                "category": cat,
                "balance_direction": norm_dir,
                "opening": 0.0,
            }

    for extra_code, extra_name in [
        ("100215", "银行存款_农商行6321"),
        ("113340", "其他应收款_昆山宇天新能源科技有限公司"),
    ]:
        if extra_code not in subjects:
            subjects[extra_code] = {
                "code": extra_code,
                "name": extra_name,
                "category": "资产",
                "balance_direction": "DEBIT",
                "opening": 0.0,
            }

    # 3. 剥离父级汇总科目期初，残差写入 {code}_00 叶子科目
    all_codes = set(subjects.keys())
    leaf_codes = {c for c in all_codes if not any(o != c and o.startswith(c) for o in all_codes)}

    for code, info in list(subjects.items()):
        if code not in leaf_codes:
            children = [c for c in leaf_codes if c.startswith(code)]
            c_sum = round(sum(subjects[c]["opening"] for c in children), 2)
            resid = round(info["opening"] - c_sum, 2)
            info["opening"] = 0.0
            if abs(resid) > 0.005:
                self_code = f"{code}_00"
                subjects[self_code] = {
                    "code": self_code,
                    "name": f"{info['name']}_自身期初",
                    "category": info["category"],
                    "balance_direction": info["balance_direction"],
                    "opening": resid,
                }
                leaf_codes.add(self_code)

    # 4. 补齐实收资本并确保 2024-01-01 期初借贷试算平衡
    if "3101" in subjects:
        subjects["3101"]["opening"] = 24081338.96
    dr_open = sum(v["opening"] for c, v in subjects.items() if c in leaf_codes and v["balance_direction"] == "DEBIT")
    cr_open = sum(v["opening"] for c, v in subjects.items() if c in leaf_codes and v["balance_direction"] == "CREDIT")
    gap = round(dr_open - cr_open, 2)
    if abs(gap) > 0.005 and "314115" in subjects:
        subjects["314115"]["opening"] = round(subjects["314115"]["opening"] + gap, 2)

    # 5. 解析 2024-01-01 及以后的全部记账凭证（仅保留末级叶子分录）
    v_rows = df_ledger.dropna(subset=["凭证字号"]).copy()
    v_rows["code"] = v_rows["科目"].astype(str).str.split("-").str[0].str.strip()
    v_rows = v_rows[v_rows["code"].isin(leaf_codes)].copy()

    vouchers_to_insert = []
    # 统计每个期间每个科目的借贷发生额，同步写入 AccountBalance 表
    period_activity: dict[tuple[str, str], list[float]] = {}

    for (dt_val, vno_raw), grp in v_rows.groupby(["日期", "凭证字号"], sort=False):
        try:
            v_date = datetime.strptime(str(dt_val).strip()[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if v_date < OPENING_CUTOFF:
            continue  # 严格拦截 2024 以前账目

        period_str = v_date.strftime("%Y-%m")
        m_num = re.search(r"(\d+)", str(vno_raw))
        v_num = int(m_num.group(1)) if m_num else 1

        entries = []
        for idx, (_, line) in enumerate(grp.iterrows(), start=1):
            dr = round(float(line["借方"]) if pd.notna(line["借方"]) else 0.0, 2)
            cr = round(float(line["贷方"]) if pd.notna(line["贷方"]) else 0.0, 2)
            code = line["code"]
            entries.append({
                "line_no": idx,
                "summary": str(line["摘要"]).strip(),
                "account_code": code,
                "debit_amount": Decimal(f"{dr:.2f}"),
                "credit_amount": Decimal(f"{cr:.2f}"),
            })
            acc_pair = period_activity.setdefault((period_str, code), [0.0, 0.0])
            acc_pair[0] = round(acc_pair[0] + dr, 2)
            acc_pair[1] = round(acc_pair[1] + cr, 2)

        vouchers_to_insert.append({
            "period": period_str,
            "voucher_no": v_num,
            "voucher_date": v_date,
            "entries": entries,
        })

    # 6. 清空旧演示库并写入 2024+ 博士特完整账套
    async with AsyncSessionLocal() as db:
        await db.execute(delete(VoucherEntry))
        await db.execute(delete(Voucher))
        await db.execute(delete(AccountBalance))
        await db.execute(delete(BankStatement))
        await db.execute(delete(BankAccount))
        await db.execute(delete(ChartOfAccount))
        await db.execute(delete(AccountSet))

        db.add(
            AccountSet(
                id=DEFAULT_ACCOUNT_SET_ID,
                company_name="博士特电子科技（昆山）有限公司（2024+新规范）",
                taxpayer_type="GENERAL",
                accounting_standard="ENT_2001",
                start_period="2024-01",
                current_period="2026-09",
            )
        )

        # 写入博士特 4 个真实银行账户
        for b_id, b_name, b_no, l_code in [
            ("00000000-0000-0000-0000-000000000010", "昆山农商行城北支行（基本户7103）", "3052220120102000007103", "100209"),
            ("00000000-0000-0000-0000-000000000011", "昆山农商行（一般户6321）", "3052220120102000006321", "100215"),
            ("00000000-0000-0000-0000-000000000012", "宁波银行昆山支行（1502）", "75090122000241502", "100211"),
            ("00000000-0000-0000-0000-000000000013", "宁波银行昆山支行（1446）", "75090122000241446", "100212"),
        ]:
            db.add(
                BankAccount(
                    id=b_id,
                    account_set_id=DEFAULT_ACCOUNT_SET_ID,
                    bank_name=b_name,
                    account_no=b_no,
                    linked_account_code=l_code,
                )
            )

        # 写入会计科目表
        for code in sorted(subjects.keys()):
            info = subjects[code]
            is_leaf = code in leaf_codes
            db.add(
                ChartOfAccount(
                    account_set_id=DEFAULT_ACCOUNT_SET_ID,
                    code=code,
                    name=info["name"],
                    pinyin_abbr=code,
                    level=1 if len(code) == 4 else 2,
                    category=info["category"],
                    balance_direction=info["balance_direction"],
                    is_leaf=is_leaf,
                    aux_types=[],
                )
            )

        # 写入 2024-01 期初余额（AccountBalance）及各月发生额汇总
        all_periods = sorted({v["period"] for v in vouchers_to_insert} | {"2024-01", "2026-09"})
        running_bal = {c: subjects[c]["opening"] for c in leaf_codes}

        for p in all_periods:
            for c in leaf_codes:
                info = subjects[c]
                open_val = running_bal[c]
                op_dr = Decimal(f"{open_val:.2f}") if info["balance_direction"] == "DEBIT" else Decimal("0.00")
                op_cr = Decimal(f"{open_val:.2f}") if info["balance_direction"] == "CREDIT" else Decimal("0.00")
                p_dr, p_cr = period_activity.get((p, c), [0.0, 0.0])

                if abs(open_val) > 0.005 or abs(p_dr) > 0.005 or abs(p_cr) > 0.005:
                    db.add(
                        AccountBalance(
                            account_set_id=DEFAULT_ACCOUNT_SET_ID,
                            period=p,
                            account_code=c,
                            aux_hash="",
                            opening_debit=op_dr,
                            opening_credit=op_cr,
                            period_debit=Decimal(f"{p_dr:.2f}"),
                            period_credit=Decimal(f"{p_cr:.2f}"),
                        )
                    )
                # 滚动计算下月期初
                if info["balance_direction"] == "DEBIT":
                    running_bal[c] = round(open_val + p_dr - p_cr, 2)
                else:
                    running_bal[c] = round(open_val + p_cr - p_dr, 2)

        # 写入 2024-01 ~ 2026-08 全部记账凭证
        for v_item in vouchers_to_insert:
            v_obj = Voucher(
                account_set_id=DEFAULT_ACCOUNT_SET_ID,
                period=v_item["period"],
                voucher_word="记",
                voucher_no=v_item["voucher_no"],
                voucher_date=v_item["voucher_date"],
                status="POSTED",
            )
            db.add(v_obj)
            await db.flush()
            for e in v_item["entries"]:
                db.add(
                    VoucherEntry(
                        voucher_id=v_obj.id,
                        line_no=e["line_no"],
                        summary=e["summary"],
                        account_code=e["account_code"],
                        debit_amount=e["debit_amount"],
                        credit_amount=e["credit_amount"],
                    )
                )

        await db.commit()

    print(f"✅ 2024+ 博士特账套全量灌入成功！")
    print(f"   - 会计科目总数：{len(subjects)} 个（其中末级叶子科目 {len(leaf_codes)} 个）")
    print(f"   - 2024-01 ~ 2026-08 记账凭证总数：{len(vouchers_to_insert)} 张（全部 >= 2024-01-01）")


if __name__ == "__main__":
    asyncio.run(run_seed())
