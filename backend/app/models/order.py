from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


transaction_id_type = BigInteger().with_variant(Integer, "sqlite")


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAYMENT_INITIATED = "payment_initiated"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PaymentAttemptStatus(StrEnum):
    CREATED = "created"
    VERIFIED = "verified"
    FAILED = "failed"


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("subtotal >= 0", name="orders_subtotal_non_negative"),
        CheckConstraint("discount >= 0", name="orders_discount_non_negative"),
        CheckConstraint("total >= 0", name="orders_total_non_negative"),
        CheckConstraint(
            "status IN ('pending', 'payment_initiated', 'paid', 'failed', 'cancelled')",
            name="orders_status_valid",
        ),
    )

    id: Mapped[int] = mapped_column(transaction_id_type, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=OrderStatus.PENDING)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    discount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    proposition_type: Mapped[str] = mapped_column(String(64), nullable=False)
    proposition_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    razorpay_order_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    # Attribution only: payment truth remains the verified payment attempt.
    commerce_session_id: Mapped[str | None] = mapped_column(String(36), index=True)
    search_request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    payment_attempts: Mapped[list["PaymentAttempt"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="order_items_quantity_positive"),
        CheckConstraint("unit_price_snapshot >= 0", name="order_items_unit_price_non_negative"),
        CheckConstraint("line_total >= 0", name="order_items_line_total_non_negative"),
    )

    id: Mapped[int] = mapped_column(transaction_id_type, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        transaction_id_type, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(
        transaction_id_type, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    product_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'verified', 'failed')", name="payment_attempts_status_valid"
        ),
    )

    id: Mapped[int] = mapped_column(transaction_id_type, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        transaction_id_type, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    razorpay_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=PaymentAttemptStatus.CREATED)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    order: Mapped[Order] = relationship(back_populates="payment_attempts")
