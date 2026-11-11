from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.product import product_id_type


product_fact_id_type = BigInteger().with_variant(Integer, "sqlite")


class ProductFact(Base):
    """A sparse, durable canonical assertion about one current Product."""

    __tablename__ = "product_facts"
    __table_args__ = (
        CheckConstraint("family IN ('role', 'attribute', 'suitability', 'use_case', 'compatibility')", name="product_facts_family_valid"),
        CheckConstraint("assertion IN ('supported', 'contradicted')", name="product_facts_assertion_valid"),
        CheckConstraint("provenance IN ('catalogue_fixture', 'merchant_asserted', 'structured_import')", name="product_facts_provenance_valid"),
        CheckConstraint("schema_version = 'v1'", name="product_facts_schema_version_valid"),
        UniqueConstraint("product_id", "family", "concept", name="product_facts_product_family_concept_unique"),
    )

    id: Mapped[int] = mapped_column(product_fact_id_type, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(product_id_type, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    concept: Mapped[str] = mapped_column(String(100), nullable=False)
    assertion: Mapped[str] = mapped_column(String(16), nullable=False, default="supported")
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(8), nullable=False, default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    product: Mapped["Product"] = relationship(back_populates="catalogue_facts")
