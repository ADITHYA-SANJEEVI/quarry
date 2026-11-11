"""Materialized, de-identified merchant demand-family projections."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.order import transaction_id_type


class ShadowDemandRefresh(Base):
    __tablename__ = "shadow_demand_refreshes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    window: Mapped[str] = mapped_column(String(8), nullable=False)
    source_observation_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    policy_versions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)


class ShadowDemandFamily(Base):
    __tablename__ = "shadow_demand_families"
    __table_args__ = (
        CheckConstraint("lifecycle_state IN ('active', 'retired')", name="shadow_demand_families_lifecycle_valid"),
        CheckConstraint("evidence_state IN ('insufficient', 'watch', 'emerging', 'established', 'strong')", name="shadow_demand_families_evidence_valid"),
        Index("ix_shadow_demand_families_active_last_seen", "lifecycle_state", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    deterministic_label: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_label: Mapped[str | None] = mapped_column(Text)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    evidence_state: Mapped[str] = mapped_column(String(16), nullable=False, default="insufficient")
    signature_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    observation_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_refresh_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("shadow_demand_refreshes.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ShadowDemandMembership(Base):
    __tablename__ = "shadow_demand_memberships"
    __table_args__ = (
        UniqueConstraint("commerce_event_id", name="shadow_demand_memberships_event_unique"),
        Index("ix_shadow_demand_memberships_family_observed", "family_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(transaction_id_type, primary_key=True, autoincrement=True)
    family_id: Mapped[str] = mapped_column(String(64), ForeignKey("shadow_demand_families.id", ondelete="CASCADE"), nullable=False)
    commerce_event_id: Mapped[str] = mapped_column(String(36), ForeignKey("commerce_events.id", ondelete="RESTRICT"), nullable=False)
    observation_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShadowDemandLineage(Base):
    __tablename__ = "shadow_demand_lineage"
    __table_args__ = (
        CheckConstraint("relation IN ('created', 'continued', 'merged', 'split', 'retired')", name="shadow_demand_lineage_relation_valid"),
        UniqueConstraint("family_id", "related_family_id", "relation", "refresh_id", name="shadow_demand_lineage_unique"),
    )

    id: Mapped[int] = mapped_column(transaction_id_type, primary_key=True, autoincrement=True)
    family_id: Mapped[str] = mapped_column(String(64), ForeignKey("shadow_demand_families.id", ondelete="CASCADE"), nullable=False, index=True)
    related_family_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("shadow_demand_families.id", ondelete="SET NULL"), index=True)
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    refresh_id: Mapped[str] = mapped_column(String(36), ForeignKey("shadow_demand_refreshes.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
