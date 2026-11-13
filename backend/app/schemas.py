from datetime import date
from decimal import Decimal
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class VoucherEntryIn(BaseModel):
    summary: str
    account_code: str
    debit_amount: Decimal = Decimal("0.00")
    credit_amount: Decimal = Decimal("0.00")
    aux_dimensions: Dict[str, Any] = Field(default_factory=dict)


class VoucherCreate(BaseModel):
    account_set_id: str
    period: str
    voucher_word: str = "记"
    voucher_date: date
    attachment_count: int = 0
    entries: List[VoucherEntryIn]


class StatementTagUpdate(BaseModel):
    suggested_account_code: Optional[str] = None
    matched_agent_id: Optional[str] = None
    counterparty_name: Optional[str] = None
    normalized_summary: Optional[str] = None
    status: Optional[str] = None


class ReverseVoucherRequest(BaseModel):
    account_set_id: str
    statement_ids: List[str]
    merge_mode: str = "ONE_TO_ONE"
    voucher_word: str = "记"
    voucher_date_strategy: str = "TX_DATE"
    overwrite_existing: bool = False