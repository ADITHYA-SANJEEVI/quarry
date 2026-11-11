"""Small owned context and prepared-action records for agent orchestration."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.commerce_memory import user_id_type


class AgentCommandContext(Base):
    __tablename__ = "agent_command_contexts"
    __table_args__ = (Index("ix_agent_context_owner_expiry", "user_id", "expires_at"),)
    commerce_session_id: Mapped[str] = mapped_column(String(36), ForeignKey("commerce_sessions.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(user_id_type, index=True)
    context_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class MerchantAgentContext(Base):
    """Owned analytical follow-up context, separate from shopper sessions."""

    __tablename__ = "merchant_agent_contexts"
    __table_args__ = (Index("ix_merchant_agent_context_expiry", "expires_at"),)

    user_id: Mapped[str] = mapped_column(user_id_type, primary_key=True)
    context_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class PreparedAgentAction(Base):
    __tablename__ = "prepared_agent_actions"
    __table_args__ = (
        CheckConstraint("action_type IN ('create_offer', 'activate_offer', 'pause_offer', 'clear_history', 'prepare_order', 'open_email_draft', 'prepare_product_draft')", name="prepared_agent_actions_type_valid"),
        CheckConstraint("status IN ('prepared', 'confirmed', 'expired')", name="prepared_agent_actions_status_valid"),
        Index("ix_prepared_agent_actions_owner_status_expiry", "owner_user_id", "status", "expires_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str | None] = mapped_column(user_id_type, index=True)
    owner_role: Mapped[str] = mapped_column(String(16), nullable=False)
    commerce_session_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("commerce_sessions.id", ondelete="SET NULL"), index=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="prepared")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
