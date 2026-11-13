import hashlib
import json
from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from ..models import (
    ChartOfAccount,
    Voucher,
    VoucherEntry,
    AccountBalance,
    ReaEvent,
)
from ..schemas import VoucherCreate


def compute_aux_hash(aux_dimensions: dict) -> str:
    if not aux_dimensions:
        return ""
    canonical = json.dumps(aux_dimensions, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()


async def create_voucher(
    db: AsyncSession, payload: VoucherCreate, rea_event_id: str | None = None
) -> Voucher:
    total_debit = sum((e.debit_amount for e in payload.entries), Decimal("0.00"))
    total_credit = sum((e.credit_amount for e in payload.entries), Decimal("0.00"))
    if total_debit != total_credit or total_debit == Decimal("0.00"):
        raise ValueError(f"凭证借贷不平衡或金额为零: 借={total_debit}, 贷={total_credit}")

    res = await db.execute(
        select(func.coalesce(func.max(Voucher.voucher_no), 0)).where(
            Voucher.account_set_id == payload.account_set_id,
            Voucher.period == payload.period,
            Voucher.voucher_word == payload.voucher_word,
        )
    )
    next_no = res.scalar_one() + 1

    if not rea_event_id:
        event = ReaEvent(
            account_set_id=payload.account_set_id,
            event_type="MANUAL_JOURNAL",
            event_date=payload.voucher_date,
            source_doc_type="MANUAL_VOUCHER",
            raw_payload={"summary": payload.entries[0].summary if payload.entries else ""},
        )
        db.add(event)
        await db.flush()
        rea_event_id = event.id

    voucher = Voucher(
        account_set_id=payload.account_set_id,
        period=payload.period,
        voucher_word=payload.voucher_word,
        voucher_no=next_no,
        voucher_date=payload.voucher_date,
        attachment_count=payload.attachment_count,
        status="POSTED",
        rea_event_id=rea_event_id,
    )
    db.add(voucher)
    await db.flush()

    for idx, item in enumerate(payload.entries, start=1):
        aux_hash = compute_aux_hash(item.aux_dimensions)
        entry = VoucherEntry(
            voucher_id=voucher.id,
            line_no=idx,
            summary=item.summary,
            account_code=item.account_code,
            debit_amount=item.debit_amount,
            credit_amount=item.credit_amount,
            aux_dimensions=item.aux_dimensions,
            aux_hash=aux_hash,
        )
        db.add(entry)
        await _update_account_balance(
            db,
            payload.account_set_id,
            payload.period,
            item.account_code,
            aux_hash,
            item.debit_amount,
            item.credit_amount,
        )

    await db.commit()
    await db.refresh(voucher)
    return voucher


async def _update_account_balance(
    db: AsyncSession,
    account_set_id: str,
    period: str,
    account_code: str,
    aux_hash: str,
    debit: Decimal,
    credit: Decimal,
):
    res = await db.execute(
        select(AccountBalance).where(
            AccountBalance.account_set_id == account_set_id,
            AccountBalance.period == period,
            AccountBalance.account_code == account_code,
            AccountBalance.aux_hash == aux_hash,
        )
    )
    bal = res.scalar_one_or_none()
    if not bal:
        bal = AccountBalance(
            account_set_id=account_set_id,
            period=period,
            account_code=account_code,
            aux_hash=aux_hash,
            opening_debit=Decimal("0.00"),
            opening_credit=Decimal("0.00"),
            period_debit=Decimal("0.00"),
            period_credit=Decimal("0.00"),
        )
        db.add(bal)
    bal.period_debit += debit
    bal.period_credit += credit


async def get_trial_balance(db: AsyncSession, account_set_id: str, period: str) -> list[dict]:
    accounts_res = await db.execute(
        select(ChartOfAccount)
        .where(ChartOfAccount.account_set_id == account_set_id)
        .order_by(ChartOfAccount.code)
    )
    accounts = accounts_res.scalars().all()

    bal_res = await db.execute(
        select(
            AccountBalance.account_code,
            func.sum(AccountBalance.opening_debit).label("od"),
            func.sum(AccountBalance.opening_credit).label("oc"),
            func.sum(AccountBalance.period_debit).label("pd"),
            func.sum(AccountBalance.period_credit).label("pc"),
        )
        .where(
            AccountBalance.account_set_id == account_set_id,
            AccountBalance.period == period,
        )
        .group_by(AccountBalance.account_code)
    )
    bal_map = {row.account_code: row for row in bal_res.all()}

    rows = []
    for acc in accounts:
        od = oc = pd = pc = Decimal("0.00")
        for code, b in bal_map.items():
            if code.startswith(acc.code):
                od += b.od or Decimal("0.00")
                oc += b.oc or Decimal("0.00")
                pd += b.pd or Decimal("0.00")
                pc += b.pc or Decimal("0.00")

        net = (od - oc) + (pd - pc)
        closing_debit = net if net > 0 else Decimal("0.00")
        closing_credit = -net if net < 0 else Decimal("0.00")

        rows.append(
            {
                "code": acc.code,
                "name": acc.name,
                "level": acc.level,
                "category": acc.category,
                "direction": acc.balance_direction,
                "opening_debit": float(od),
                "opening_credit": float(oc),
                "period_debit": float(pd),
                "period_credit": float(pc),
                "closing_debit": float(closing_debit),
                "closing_credit": float(closing_credit),
            }
        )
    return rows