from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


proposition_id_type = BigInteger().with_variant(Integer, "sqlite")


class GeneratedPropositionStatus(StrEnum):
    AVAILABLE = "available"
    CONSUMED = "consumed"
    EXPIRED = "expired"


class CommercePropositionRecord(Base):
    __tablename__ = "commerce_propositions"
    __table_args__ = (
        CheckConstraint("subtotal >= 0", name="commerce_propositions_subtotal_non_negative"),
        CheckConstraint("discount >= 0", name="commerce_propositions_discount_non_negative"),
        CheckConstraint("total >= 0", name="commerce_propositions_total_non_negative"),
        CheckConstraint(
            "status IN ('available', 'consumed', 'expired')",
            name="commerce_propositions_status_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    merchant_offer_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("merchant_offers.id", ondelete="RESTRICT"), index=True
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    discount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    intent_reference: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    intent_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    evidence_json: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=GeneratedPropositionStatus.AVAILABLE, index=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["CommercePropositionItemRecord"]] = relationship(
        back_populates="proposition", cascade="all, delete-orphan"
    )


class CommercePropositionItemRecord(Base):
    __tablename__ = "commerce_proposition_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="commerce_proposition_items_quantity_positive"),
        CheckConstraint(
            "unit_price_snapshot >= 0",
            name="commerce_proposition_items_unit_price_non_negative",
        ),
        CheckConstraint(
            "line_total >= 0", name="commerce_proposition_items_line_total_non_negative"
        ),
    )

    id: Mapped[int] = mapped_column(proposition_id_type, primary_key=True, autoincrement=True)
    proposition_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("commerce_propositions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[int] = mapped_column(
        proposition_id_type, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    product_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    proposition: Mapped[CommercePropositionRecord] = relationship(back_populates="items")
