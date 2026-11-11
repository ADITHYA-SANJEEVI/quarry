from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateOrderRequest(BaseModel):
    product_id: int | None = Field(default=None, gt=0)
    proposition_id: str | None = Field(default=None, min_length=1, max_length=36)
    commerce_session_id: str | None = Field(default=None, min_length=36, max_length=36)
    anonymous_session_credential: str | None = Field(default=None, min_length=16, max_length=256)
    search_request_id: str | None = Field(default=None, min_length=36, max_length=36)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def exactly_one_purchase_target(self) -> "CreateOrderRequest":
        if (self.product_id is None) == (self.proposition_id is None):
            raise ValueError("Exactly one of product_id or proposition_id is required.")
        return self


class PropositionSummary(BaseModel):
    reference: str
    type: str
    display_name: str
    total: Decimal
    currency: str


class CheckoutOrderResponse(BaseModel):
    internal_order_id: int
    razorpay_order_id: str
    amount: int = Field(ge=0, description="Amount in paise")
    currency: str
    key_id: str
    commerce_session_id: str
    anonymous_session_credential: str | None = None
    proposition: PropositionSummary


class OrderItemRead(BaseModel):
    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal


class OrderRead(BaseModel):
    id: int
    status: str
    currency: str
    total: Decimal
    proposition_type: str
    proposition_reference: str
    items: list[OrderItemRead]
    payment_status: str | None
    payment_presentation_state: str
    payment_authoritative: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrderSummaryRead(BaseModel):
    id: int
    created_at: datetime
    proposition_type: str
    proposition_reference: str
    total: Decimal
    currency: str
    payment_presentation_state: str
    payment_authoritative: bool = False


class OrderListRead(BaseModel):
    items: list[OrderSummaryRead]
