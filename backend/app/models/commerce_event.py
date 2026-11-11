from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.order import transaction_id_type
from app.models.commerce_memory import user_id_type


class CommerceSessionStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class CommerceEventType(StrEnum):
    SESSION_STARTED = "session_started"
    SEARCH_SUBMITTED = "search_submitted"
    SEARCH_REFINED = "search_refined"
    RESULTS_VIEWED = "results_viewed"
    PRODUCT_CLICKED = "product_clicked"
    PROPOSITION_GENERATED = "proposition_generated"
    PROPOSITION_VIEWED = "proposition_viewed"
    CHECKOUT_STARTED = "checkout_started"
    CHECKOUT_CANCELLED = "checkout_cancelled"
    PAYMENT_VERIFIED = "payment_verified"
    NO_MATCH = "no_match"
    COMPILER_NO_FEASIBLE = "compiler_no_feasible"


class CommerceEventSource(StrEnum):
    BACKEND = "backend"
    CLIENT = "client"


class CommerceSession(Base):
    __tablename__ = "commerce_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'closed')", name="commerce_sessions_status_valid"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CommerceSessionStatus.ACTIVE)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Identity/memory fields are intentionally nullable for pre-3D sessions.
    user_id: Mapped[str | None] = mapped_column(user_id_type, index=True)
    anonymous_credential_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    anonymous_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    canonical_intent_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    memory_policy_version: Mapped[str] = mapped_column(String(64), nullable=False, default="commerce_memory_policy_v1")
    latest_decision_mechanism: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(nullable=False, default=1)

    events: Mapped[list["CommerceEvent"]] = relationship(back_populates="session")


class CommerceEvent(Base):
    __tablename__ = "commerce_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('session_started', 'search_submitted', 'search_refined', "
            "'results_viewed', 'product_clicked', 'proposition_generated', "
            "'proposition_viewed', 'checkout_started', 'checkout_cancelled', "
            "'payment_verified', 'no_match', 'compiler_no_feasible')",
            name="commerce_events_type_valid",
        ),
        CheckConstraint("source IN ('backend', 'client')", name="commerce_events_source_valid"),
        UniqueConstraint("session_id", "client_event_id", name="commerce_events_session_client_event_unique"),
        UniqueConstraint("order_id", "event_type", name="commerce_events_order_type_unique"),
        Index("ix_commerce_events_session_occurred_at", "session_id", "occurred_at"),
        Index("ix_commerce_events_type_occurred_at", "event_type", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("commerce_sessions.id", ondelete="RESTRICT"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    search_request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    product_id: Mapped[int | None] = mapped_column(
        transaction_id_type, ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    proposition_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("commerce_propositions.id", ondelete="RESTRICT"), index=True
    )
    order_id: Mapped[int | None] = mapped_column(
        transaction_id_type, ForeignKey("orders.id", ondelete="RESTRICT"), index=True
    )
    event_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")
    client_event_id: Mapped[str | None] = mapped_column(String(36))

    session: Mapped[CommerceSession | None] = relationship(back_populates="events")


class PaymentCommerceAttribution(Base):
    """Committed payment provenance, independent of retryable event projection."""

    __tablename__ = "payment_commerce_attributions"
    __table_args__ = (
        UniqueConstraint("order_id", name="payment_commerce_attributions_order_unique"),
        UniqueConstraint("payment_attempt_id", name="payment_commerce_attributions_attempt_unique"),
    )

    id: Mapped[int] = mapped_column(transaction_id_type, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(transaction_id_type, ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True)
    payment_attempt_id: Mapped[int] = mapped_column(transaction_id_type, ForeignKey("payment_attempts.id", ondelete="RESTRICT"), nullable=False, index=True)
    proposition_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("commerce_propositions.id", ondelete="RESTRICT"), index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("commerce_sessions.id", ondelete="RESTRICT"), index=True)
    search_request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    mechanism: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
