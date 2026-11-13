import io
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..models import ChartOfAccount, Voucher, VoucherEntry


async def export_xiaokuaiji_compatible_excel(
    db: AsyncSession, account_set_id: str, period: str
) -> bytes:
    acc_res = await db.execute(
        select(ChartOfAccount)
        .where(ChartOfAccount.account_set_id == account_set_id)
        .order_by(ChartOfAccount.code)
    )
    accounts = acc_res.scalars().all()

    v_res = await db.execute(
        select(Voucher)
        .where(Voucher.account_set_id == account_set_id, Voucher.period == period)
        .order_by(Voucher.voucher_no)
    )
    vouchers = v_res.scalars().all()

    acc_rows = [
        {
            "科目编码": a.code,
            "科目名称": a.name,
            "科目类别": a.category,
            "余额方向": "借" if a.balance_direction == "DEBIT" else "贷",
            "辅助核算": ",".join(a.aux_types or []),
        }
        for a in accounts
    ]

    v_rows = []
    for v in vouchers:
        e_res = await db.execute(
            select(VoucherEntry)
            .where(VoucherEntry.voucher_id == v.id)
            .order_by(VoucherEntry.line_no)
        )
        for e in e_res.scalars().all():
            v_rows.append(
                {
                    "日期": v.voucher_date.strftime("%Y-%m-%d"),
                    "凭证字": v.voucher_word,
                    "凭证号": v.voucher_no,
                    "摘要": e.summary,
                    "科目编码": e.account_code,
                    "借方金额": float(e.debit_amount),
                    "贷方金额": float(e.credit_amount),
                }
            )

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(acc_rows).to_excel(writer, sheet_name="会计科目表", index=False)
        pd.DataFrame(v_rows).to_excel(writer, sheet_name="记账凭证", index=False)
    buf.seek(0)
    return buf.read()


async def import_xiaokuaiji_excel(
    db: AsyncSession, account_set_id: str, content: bytes
) -> dict:
    xls = pd.ExcelFile(io.BytesIO(content))
    imported_accounts = 0

    if "会计科目表" in xls.sheet_names:
        df_acc = pd.read_excel(xls, sheet_name="会计科目表").dropna(subset=["科目编码", "科目名称"])
        for _, row in df_acc.iterrows():
            code = str(row["科目编码"]).strip()
            name = str(row["科目名称"]).strip()
            exist = await db.execute(
                select(ChartOfAccount).where(
                    ChartOfAccount.account_set_id == account_set_id,
                    ChartOfAccount.code == code,
                )
            )
            if not exist.scalar_one_or_none():
                direction = "DEBIT" if str(row.get("余额方向", "借")).strip() == "借" else "CREDIT"
                db.add(
                    ChartOfAccount(
                        account_set_id=account_set_id,
                        code=code,
                        name=name,
                        level=1 if len(code) == 4 else 2,
                        category=str(row.get("科目类别", "资产")).strip(),
                        balance_direction=direction,
                        is_leaf=True,
                    )
                )
                imported_accounts += 1

    await db.commit()
    return {"status": "SUCCESS", "imported_accounts": imported_accounts}