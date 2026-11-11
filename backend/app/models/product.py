from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Integer, JSON, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


product_id_type = BigInteger().with_variant(Integer, "sqlite")
product_tags_type = ARRAY(Text).with_variant(JSON, "sqlite")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price >= 0", name="products_price_non_negative"),
        CheckConstraint("stock >= 0", name="products_stock_non_negative"),
    )

    id: Mapped[int] = mapped_column(product_id_type, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, index=True)
    stock: Mapped[int] = mapped_column(nullable=False)
    tags: Mapped[list[str]] = mapped_column(product_tags_type, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true", index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_demand_family_id: Mapped[str | None] = mapped_column(Text, index=True)
    source_opportunity_id: Mapped[str | None] = mapped_column(Text, index=True)
    catalogue_facts: Mapped[list["ProductFact"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", lazy="selectin"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @property
    def catalogue_active(self) -> bool:
        """Archive state is authoritative even for an inconsistent legacy row.

        Transient fixture products have not received SQLAlchemy's insert-time
        True default yet. Only persisted rows require a non-null is_active.
        """
        return self.is_active is not False and self.archived_at is None


# Register the related mapper for callers that import Product directly (the
# common test/runtime path), while retaining string annotations above.
from app.models.product_fact import ProductFact  # noqa: E402, F401
