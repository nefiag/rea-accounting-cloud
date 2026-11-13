from collections import defaultdict
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..models import BankAccount, BankStatement, ReaEvent, Voucher
from ..schemas import VoucherCreate, VoucherEntryIn
from .ledger_service import create_voucher


async def synthesize_vouchers_from_statements(
    db: AsyncSession,
    account_set_id: str,
    statement_ids: list[str],
    merge_mode: str = "ONE_TO_ONE",
    voucher_word: str = "记",
    overwrite_existing: bool = False,
) -> list[Voucher]:
    res = await db.execute(
        select(BankStatement)
        .where(
            BankStatement.account_set_id == account_set_id,
            BankStatement.id.in_(statement_ids),
        )
        .order_by(BankStatement.tx_date.asc())
    )
    statements = res.scalars().all()
    if not statements:
        return []

    target_stmts = [
        s for s in statements if overwrite_existing or not s.voucher_id
    ]
    if not target_stmts:
        return []

    bank_acc_ids = {s.bank_account_id for s in target_stmts}
    ba_res = await db.execute(select(BankAccount).where(BankAccount.id.in_(bank_acc_ids)))
    bank_acc_map = {b.id: b for b in ba_res.scalars().all()}

    groups: dict[str, list[BankStatement]] = defaultdict(list)
    if merge_mode == "ONE_TO_ONE":
        for s in target_stmts:
            groups[s.id].append(s)
    elif merge_mode == "MERGE_BY_CATEGORY":
        for s in target_stmts:
            cat = (s.ai_tags or {}).get("intent_category", "OTHER")
            groups[f"{s.bank_account_id}_{cat}_{s.direction}"].append(s)
    else:
        for s in target_stmts:
            groups[s.bank_account_id].append(s)

    created_vouchers: list[Voucher] = []

    for _, group_items in groups.items():
        first = group_items[0]
        bank_acc = bank_acc_map.get(first.bank_account_id)
        bank_subject_code = bank_acc.linked_account_code if bank_acc else "100201"
        tx_day = first.tx_date.date()
        period = tx_day.strftime("%Y-%m")

        rea_event = ReaEvent(
            account_set_id=account_set_id,
            event_type="BANK_SETTLEMENT_BATCH" if len(group_items) > 1 else "BANK_SETTLEMENT",
            event_date=tx_day,
            source_doc_type="BANK_STATEMENT",
            raw_payload={"statement_ids": [s.id for s in group_items]},
        )
        db.add(rea_event)
        await db.flush()

        entries: list[VoucherEntryIn] = []
        for s in group_items:
            summary = (s.ai_tags or {}).get("normalized_summary") or s.raw_summary or "银行资金往来"
            aux = {"AGENT": s.matched_agent_id} if s.matched_agent_id else {}
            counter_code = s.suggested_account_code or "1122"

            if s.direction == "IN":
                entries.append(
                    VoucherEntryIn(
                        summary=summary,
                        account_code=bank_subject_code,
                        debit_amount=s.amount,
                        credit_amount=Decimal("0.00"),
                    )
                )
                entries.append(
                    VoucherEntryIn(
                        summary=summary,
                        account_code=counter_code,
                        debit_amount=Decimal("0.00"),
                        credit_amount=s.amount,
                        aux_dimensions=aux,
                    )
                )
            else:
                entries.append(
                    VoucherEntryIn(
                        summary=summary,
                        account_code=counter_code,
                        debit_amount=s.amount,
                        credit_amount=Decimal("0.00"),
                        aux_dimensions=aux,
                    )
                )
                entries.append(
                    VoucherEntryIn(
                        summary=summary,
                        account_code=bank_subject_code,
                        debit_amount=Decimal("0.00"),
                        credit_amount=s.amount,
                    )
                )

        v_payload = VoucherCreate(
            account_set_id=account_set_id,
            period=period,
            voucher_word=voucher_word,
            voucher_date=tx_day,
            attachment_count=len(group_items),
            entries=entries,
        )
        voucher = await create_voucher(db, v_payload, rea_event_id=rea_event.id)

        for s in group_items:
            s.rea_event_id = rea_event.id
            s.voucher_id = voucher.id
            s.status = "POSTED"

        await db.commit()
        created_vouchers.append(voucher)

    return created_vouchers