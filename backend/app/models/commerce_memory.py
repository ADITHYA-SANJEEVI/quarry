"""Server-owned identity and shopper commerce-memory records.

These models deliberately keep display history separate from current commerce
truth.  The structured snapshot is replayed through the decision engine.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

user_id_type = String(36).with_variant(Uuid(as_uuid=False), "postgresql")


class ShopperProfile(Base):
    __tablename__ = "shopper_profiles"
    __table_args__ = (CheckConstraint("role IN ('shopper', 'merchant')", name="shopper_profiles_role_valid"),)

    user_id: Mapped[str] = mapped_column(user_id_type, primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="shopper")
    display_name: Mapped[str | None] = mapped_column(String(120))
    # Non-sensitive browser presentation preferences. Commerce decisions never
    # read this owner-scoped field.
    preferences: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="conversation_turns_role_valid"),
        CheckConstraint("input_modality IN ('text', 'voice')", name="conversation_turns_modality_valid"),
        Index("ix_conversation_turns_session_created_id", "session_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("commerce_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(12), nullable=False)
    input_modality: Mapped[str] = mapped_column(String(12), nullable=False, default="text")
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_text: Mapped[str | None] = mapped_column(Text)
    source_language: Mapped[str | None] = mapped_column(String(16))
    provenance_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ShopperCommerceHistory(Base):
    __tablename__ = "shopper_commerce_history"
    __table_args__ = (
        Index("ix_shopper_commerce_history_user_created_id", "user_id", "created_at", "id"),
        Index("ix_shopper_commerce_history_session_id", "session_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(user_id_type, ForeignKey("shopper_profiles.user_id", ondelete="RESTRICT"), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("commerce_sessions.id", ondelete="SET NULL"))
    original_source_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_language: Mapped[str | None] = mapped_column(String(16))
    input_modality: Mapped[str] = mapped_column(String(12), nullable=False, default="text")
    canonical_intent_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    memory_policy_version: Mapped[str] = mapped_column(String(64), nullable=False, default="commerce_memory_policy_v1")
    decision_mechanism: Mapped[str] = mapped_column(String(64), nullable=False)
    adequacy_state: Mapped[str] = mapped_column(String(64), nullable=False)
    display_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    selected_proposition_id: Mapped[str | None] = mapped_column(String(36))
    checkout_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
