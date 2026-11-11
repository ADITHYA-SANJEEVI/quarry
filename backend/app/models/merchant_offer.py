"""Durable merchant-authored offer configuration, separate from sellable snapshots."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.product import product_id_type


class MerchantOfferStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"


class MerchantOfferOrigin(StrEnum):
    DEMAND_DERIVED = "demand_derived"
    MERCHANT_CREATED = "merchant_created"


class MerchantOfferActionType(StrEnum):
    CREATED = "created"
    ACTIVATED = "activated"
    PAUSED = "paused"


offer_action_id_type = BigInteger().with_variant(Integer, "sqlite")


class MerchantOffer(Base):
    __tablename__ = "merchant_offers"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'active', 'paused')", name="merchant_offers_status_valid"),
        CheckConstraint("origin IN ('demand_derived', 'merchant_created')", name="merchant_offers_origin_valid"),
        UniqueConstraint(
            "source_opportunity_id",
            "component_fingerprint",
            name="merchant_offers_source_components_unique",
        ),
        UniqueConstraint("manual_idempotency_key", name="merchant_offers_manual_idempotency_key_unique"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    origin: Mapped[str] = mapped_column(String(24), nullable=False, default=MerchantOfferOrigin.DEMAND_DERIVED, index=True)
    source_opportunity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_intent_signature_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    component_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=MerchantOfferStatus.DRAFT, index=True)
    manual_idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manual_idempotency_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    items: Mapped[list["MerchantOfferItem"]] = relationship(
        back_populates="offer", cascade="all, delete-orphan", order_by="MerchantOfferItem.position"
    )
    actions: Mapped[list["MerchantOfferAction"]] = relationship(
        back_populates="offer", cascade="all, delete-orphan", order_by="MerchantOfferAction.id"
    )


class MerchantOfferItem(Base):
    __tablename__ = "merchant_offer_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="merchant_offer_items_quantity_positive"),
        UniqueConstraint("merchant_offer_id", "position", name="merchant_offer_items_position_unique"),
        UniqueConstraint("merchant_offer_id", "product_id", name="merchant_offer_items_product_unique"),
    )

    id: Mapped[int] = mapped_column(offer_action_id_type, primary_key=True, autoincrement=True)
    merchant_offer_id: Mapped[str] = mapped_column(String(36), ForeignKey("merchant_offers.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(product_id_type, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    offer: Mapped[MerchantOffer] = relationship(back_populates="items")


class MerchantOfferAction(Base):
    __tablename__ = "merchant_offer_actions"
    __table_args__ = (
        CheckConstraint("action_type IN ('created', 'activated', 'paused')", name="merchant_offer_actions_type_valid"),
    )

    id: Mapped[int] = mapped_column(offer_action_id_type, primary_key=True, autoincrement=True)
    merchant_offer_id: Mapped[str] = mapped_column(String(36), ForeignKey("merchant_offers.id", ondelete="CASCADE"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(24), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(16))
    next_status: Mapped[str] = mapped_column(String(16), nullable=False)
    action_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    offer: Mapped[MerchantOffer] = relationship(back_populates="actions")
