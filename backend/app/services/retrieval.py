from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.product import Product
from app.schemas.intent import ShoppingIntent


@dataclass(frozen=True)
class RetrievalResult:
    candidates: list[Product]
    available_count: int


class CandidateRetriever(Protocol):
    def retrieve(self, session: Session, intent: ShoppingIntent) -> RetrievalResult:
        ...


class PostgresCatalogueRetriever:
    """Loads merchant-authoritative candidates before the v2 eligibility gate.

    Price, exclusion and compatibility policy lives in ConstraintEngine so both
    the pre-retrieval and final safety gates use one deterministic definition.
    """

    def retrieve(self, session: Session, intent: ShoppingIntent) -> RetrievalResult:
        available_count = session.scalar(
            select(func.count()).select_from(Product).where(
                Product.stock > 0, Product.is_active.is_(True), Product.archived_at.is_(None)
            )
        ) or 0
        # One secondary select loads sparse facts for the full candidate set;
        # evidence/ranking never performs a per-product fact query.
        statement = select(Product).options(selectinload(Product.catalogue_facts))
        return RetrievalResult(
            candidates=list(session.scalars(statement)),
            available_count=available_count,
        )
