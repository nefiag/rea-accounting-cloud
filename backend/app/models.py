import uuid
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import (
    String,
    Integer,
    Boolean,
    Date,
    DateTime,
    Numeric,
    ForeignKey,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


def gen_uuid():
    return str(uuid.uuid4())


class AccountSet(Base):
    __tablename__ = "tenants_account_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    taxpayer_type: Mapped[str] = mapped_column(String(20), default="GENERAL")
    accounting_standard: Mapped[str] = mapped_column(String(30), default="SMALL_ENT_2013")
    start_period: Mapped[str] = mapped_column(String(7), default="2026-01")
    current_period: Mapped[str] = mapped_column(String(7), default="2026-11")
    code_rule: Mapped[str] = mapped_column(String(32), default="4-2-2-2")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChartOfAccount(Base):
    __tablename__ = "chart_of_accounts"
    __table_args__ = (UniqueConstraint("account_set_id", "code", name="uq_account_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    account_set_id: Mapped[str] = mapped_column(ForeignKey("tenants_account_sets.id"), index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    pinyin_abbr: Mapped[str] = mapped_column(String(64), default="")
    parent_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    category: Mapped[str] = mapped_column(String(32), default="资产")
    balance_direction: Mapped[str] = mapped_column(String(10), default="DEBIT")
    is_leaf: Mapped[bool] = mapped_column(Boolean, default=True)
    aux_types: Mapped[list] = mapped_column(JSONB, default=list)


class ReaResource(Base):
    __tablename__ = "rea_resources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    account_set_id: Mapped[str] = mapped_column(ForeignKey("tenants_account_sets.id"), index=True)
    resource_type: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    linked_account_code: Mapped[str] = mapped_column(String(32))
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)


class ReaAgent(Base):
    __tablename__ = "rea_agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    account_set_id: Mapped[str] = mapped_column(ForeignKey("tenants_account_sets.id"), index=True)
    agent_type: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), index=True)
    tax_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extra_meta: Mapped[dict] = mapped_column(JSONB, default=dict)


class ReaEvent(Base):
    __tablename__ = "rea_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    account_set_id: Mapped[str] = mapped_column(ForeignKey("tenants_account_sets.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    event_date: Mapped[date] = mapped_column(Date)
    duality_group_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_doc_type: Mapped[str] = mapped_column(String(32), default="MANUAL_VOUCHER")
    raw_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Voucher(Base):
    __tablename__ = "vouchers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    account_set_id: Mapped[str] = mapped_column(ForeignKey("tenants_account_sets.id"), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    voucher_word: Mapped[str] = mapped_column(String(10), default="记")
    voucher_no: Mapped[int] = mapped_column(Integer, nullable=False)
    voucher_date: Mapped[date] = mapped_column(Date, nullable=False)
    attachment_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    rea_event_id: Mapped[str | None] = mapped_column(ForeignKey("rea_events.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    entries: Mapped[list["VoucherEntry"]] = relationship(
        "VoucherEntry", back_populates="voucher", cascade="all, delete-orphan"
    )


class VoucherEntry(Base):
    __tablename__ = "voucher_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    voucher_id: Mapped[str] = mapped_column(ForeignKey("vouchers.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(String(255), default="")
    account_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    debit_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    credit_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    aux_dimensions: Mapped[dict] = mapped_column(JSONB, default=dict)
    aux_hash: Mapped[str] = mapped_column(String(64), default="")

    voucher: Mapped["Voucher"] = relationship("Voucher", back_populates="entries")


class AccountBalance(Base):
    __tablename__ = "account_balances"
    __table_args__ = (
        UniqueConstraint("account_set_id", "period", "account_code", "aux_hash", name="uq_bal_dim"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    account_set_id: Mapped[str] = mapped_column(ForeignKey("tenants_account_sets.id"), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    account_code: Mapped[str] = mapped_column(String(32), index=True)
    aux_hash: Mapped[str] = mapped_column(String(64), default="")
    opening_debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    opening_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    period_debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))
    period_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0.00"))


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    account_set_id: Mapped[str] = mapped_column(ForeignKey("tenants_account_sets.id"), index=True)
    bank_name: Mapped[str] = mapped_column(String(100), nullable=False)
    account_no: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="CNY")
    linked_account_code: Mapped[str] = mapped_column(String(32), default="100201")
    rea_resource_id: Mapped[str | None] = mapped_column(ForeignKey("rea_resources.id"), nullable=True)


class BankStatement(Base):
    __tablename__ = "bank_statements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=gen_uuid)
    account_set_id: Mapped[str] = mapped_column(ForeignKey("tenants_account_sets.id"), index=True)
    bank_account_id: Mapped[str] = mapped_column(ForeignKey("bank_accounts.id"), index=True)
    tx_fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tx_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    bank_serial_no: Mapped[str] = mapped_column(String(128), default="")
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    balance_after: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    counterparty_name: Mapped[str] = mapped_column(String(200), default="")
    counterparty_account: Mapped[str] = mapped_column(String(64), default="")
    raw_summary: Mapped[str] = mapped_column(Text, default="")
    ai_tags: Mapped[dict] = mapped_column(JSONB, default=dict)
    matched_agent_id: Mapped[str | None] = mapped_column(ForeignKey("rea_agents.id"), nullable=True)
    suggested_account_code: Mapped[str] = mapped_column(String(32), default="1122")
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.8000"))
    rea_event_id: Mapped[str | None] = mapped_column(ForeignKey("rea_events.id"), nullable=True)
    voucher_id: Mapped[str | None] = mapped_column(ForeignKey("vouchers.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PARSED")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)