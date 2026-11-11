"""Bounded merchant-owned catalogue and approved-outreach records."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


control_id_type = BigInteger().with_variant(Integer, "sqlite")


class ApprovedBrandContact(Base):
    __tablename__ = "approved_brand_contacts"
    __table_args__ = (UniqueConstraint("owner_user_id", "email", name="approved_brand_contacts_owner_email_unique"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    company_name: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(Text)
    allowed_to_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class MerchantOutreachDraft(Base):
    __tablename__ = "merchant_outreach_drafts"
    __table_args__ = (
        CheckConstraint("status IN ('prepared', 'opened')", name="merchant_outreach_drafts_status_valid"),
        Index("ix_merchant_outreach_drafts_owner_status", "owner_user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    brand_contact_id: Mapped[str] = mapped_column(String(36), ForeignKey("approved_brand_contacts.id", ondelete="RESTRICT"), nullable=False, index=True)
    prepared_action_id: Mapped[str] = mapped_column(String(36), ForeignKey("prepared_agent_actions.id", ondelete="RESTRICT"), nullable=False, unique=True, index=True)
    source_demand_family_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_opportunity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="prepared")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class MerchantControlAction(Base):
    __tablename__ = "merchant_control_actions"
    __table_args__ = (Index("ix_merchant_control_actions_product_created", "product_id", "created_at"),)

    id: Mapped[int] = mapped_column(control_id_type, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_demand_family_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_opportunity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
