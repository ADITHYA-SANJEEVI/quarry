from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ClientEventType = Literal[
    "results_viewed",
    "product_clicked",
    "proposition_viewed",
    "checkout_started",
    "checkout_cancelled",
]


class CommerceSessionRequest(BaseModel):
    commerce_session_id: UUID | None = None

    model_config = ConfigDict(extra="forbid")


class CommerceSessionRead(BaseModel):
    commerce_session_id: UUID
    started_at: datetime
    last_activity_at: datetime
    status: str


class ClientCommerceEventRequest(BaseModel):
    event_id: UUID
    commerce_session_id: UUID
    event_type: ClientEventType
    search_request_id: UUID | None = None
    product_id: int | None = Field(default=None, gt=0)
    proposition_id: UUID | None = None
    order_id: int | None = Field(default=None, gt=0)
    metadata: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("metadata")
    @classmethod
    def metadata_is_compact(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 8:
            raise ValueError("metadata must contain at most 8 fields.")
        if any(len(key) > 48 or len(item) > 120 for key, item in value.items()):
            raise ValueError("metadata fields are too long.")
        return value

    @model_validator(mode="after")
    def validate_references(self) -> "ClientCommerceEventRequest":
        if self.event_type in {"results_viewed", "product_clicked", "proposition_viewed"} and self.search_request_id is None:
            raise ValueError("search_request_id is required for this event.")
        if self.event_type == "product_clicked" and self.product_id is None:
            raise ValueError("product_id is required for product_clicked.")
        if self.event_type == "proposition_viewed" and self.proposition_id is None:
            raise ValueError("proposition_id is required for proposition_viewed.")
        if self.event_type in {"checkout_started", "checkout_cancelled"} and self.order_id is None:
            raise ValueError("order_id is required for checkout events.")
        return self


class CommerceEventAccepted(BaseModel):
    accepted: bool
    duplicate: bool = False


class BehavioralRoleInterest(BaseModel):
    role: str
    confidence_band: str
    provenance: str


class BehavioralIntentRead(BaseModel):
    viewed_roles: list[str] = Field(default_factory=list)
    clicked_roles: list[str] = Field(default_factory=list)
    rejected_or_ignored_roles: list[str] = Field(default_factory=list)
    price_band_observed: str | None = None
    proposition_interest: bool = False
    role_interests: list[BehavioralRoleInterest] = Field(default_factory=list)
