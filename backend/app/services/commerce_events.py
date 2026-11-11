"""Append-only, privacy-bounded commerce interaction records for Increment 6."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.commerce_event import (
    CommerceEvent,
    CommerceEventSource,
    CommerceEventType,
    CommerceSession,
    CommerceSessionStatus,
    PaymentCommerceAttribution,
)
from app.models.commerce_proposition import CommercePropositionRecord
from app.models.order import Order
from app.models.product import Product
from app.schemas.commerce_events import ClientCommerceEventRequest
from app.schemas.intent import ShoppingIntent


CLIENT_EVENT_TYPES = {
    CommerceEventType.RESULTS_VIEWED,
    CommerceEventType.PRODUCT_CLICKED,
    CommerceEventType.PROPOSITION_VIEWED,
    CommerceEventType.CHECKOUT_STARTED,
    CommerceEventType.CHECKOUT_CANCELLED,
}


@dataclass(frozen=True)
class BehavioralRoleInterest:
    role: str
    confidence_band: str
    provenance: str
    boost: float


@dataclass(frozen=True)
class BehavioralIntentSummary:
    viewed_roles: tuple[str, ...] = ()
    clicked_roles: tuple[str, ...] = ()
    rejected_or_ignored_roles: tuple[str, ...] = ()
    price_band_observed: str | None = None
    proposition_interest: bool = False
    role_interests: tuple[BehavioralRoleInterest, ...] = ()

    def boost_for(self, product: Product) -> float:
        roles = product_roles(product)
        return max((item.boost for item in self.role_interests if item.role in roles), default=0.0)


def product_roles(product: Product) -> set[str]:
    tags = set(product.tags)
    known = {"cleanser", "moisturiser", "sunscreen", "charger", "cable", "power-bank"}
    roles = known & tags
    if not roles and product.category:
        roles.add(product.category.rstrip("s"))
    return roles


def safe_intent_snapshot(intent: ShoppingIntent, parser_source: str) -> dict:
    """Store normalized analytical signals, deliberately excluding raw query text."""
    return {
        # Category is already a bounded deterministic intent field.  Keeping
        # it in the safe snapshot makes category-scoped aggregate analysis
        # possible without retaining shopper wording.
        "category": intent.category,
        "functional_roles": [signal.value for signal in intent.product_concepts],
        "explicit_attributes": [signal.value for signal in intent.desired_attributes],
        "exclusions": [signal.value for signal in intent.hard_exclusions],
        "strict_min_price": str(intent.strict_min_price) if intent.strict_min_price is not None else None,
        "strict_max_price": str(intent.strict_max_price) if intent.strict_max_price is not None else None,
        "target_price": str(intent.target_price) if intent.target_price is not None else None,
        "gifting_intent": intent.gifting_intent,
        # The interpreter may supply a contextual recipient string. Persist
        # only the deterministic parser's bounded taxonomy, never arbitrary
        # provider/customer text.
        "recipient": intent.recipient if intent.recipient in {"mother", "father", "student"} else None,
        "occasion": intent.occasion,
        "clarification_needed": intent.clarification_needed,
        # Additive typed semantics for analytics. This remains a safe canonical
        # snapshot: no raw wording, provider output, customer identifier, or
        # payment data is persisted here.
        "role_clauses": [clause.model_dump(mode="json") for clause in intent.role_clauses],
        "requirements": [requirement.model_dump(mode="json") for requirement in intent.requirements],
        "budgets": [budget.model_dump(mode="json") for budget in intent.budgets],
        "compatibility_requirements": [signal.model_dump(mode="json") for signal in intent.compatibility_requirements],
        "parser_source": parser_source,
        "intent_schema_version": "v3",
    }


class CommerceEventService:
    def create_or_recover_session(self, session: Session, session_id: str | None = None) -> CommerceSession:
        record = session.get(CommerceSession, session_id) if session_id else None
        if record is None:
            record = CommerceSession(id=session_id or str(uuid4()), status=CommerceSessionStatus.ACTIVE, metadata_json={})
            session.add(record)
            session.flush()
            self._append(
                session,
                event_type=CommerceEventType.SESSION_STARTED,
                session_id=record.id,
                source=CommerceEventSource.BACKEND,
                event_data={},
            )
            session.commit()
        elif record.status != CommerceSessionStatus.ACTIVE:
            raise ValueError("Commerce session is not active.")
        else:
            record.last_activity_at = datetime.now(UTC)
            session.commit()
        return record

    def get_summary(self, session: Session, session_id: str | None) -> BehavioralIntentSummary:
        if not session_id or session.get(CommerceSession, session_id) is None:
            return BehavioralIntentSummary()
        events = list(
            session.scalars(
                select(CommerceEvent)
                .where(CommerceEvent.session_id == session_id)
                .order_by(CommerceEvent.occurred_at, CommerceEvent.id)
            )
        )
        product_ids = {event.product_id for event in events if event.product_id is not None}
        products = {
            product.id: product
            for product in session.scalars(select(Product).where(Product.id.in_(product_ids)))
        } if product_ids else {}
        viewed_roles: set[str] = set()
        clicked_counts: dict[str, int] = {}
        price_values = []
        proposition_interest = False
        for event in events:
            if event.event_type == CommerceEventType.SEARCH_SUBMITTED:
                viewed_roles.update(event.event_data.get("intent", {}).get("functional_roles", []))
            if event.event_type == CommerceEventType.PRODUCT_CLICKED and event.product_id in products:
                product = products[event.product_id]
                for role in product_roles(product):
                    clicked_counts[role] = clicked_counts.get(role, 0) + 1
                price_values.append(product.price)
            if event.event_type == CommerceEventType.PROPOSITION_VIEWED:
                proposition_interest = True

        interests = tuple(
            BehavioralRoleInterest(
                role=role,
                confidence_band="inferred_medium" if count >= 2 else "inferred_low",
                provenance=f"{count} current-session product click{'s' if count != 1 else ''}",
                boost=0.06 if count >= 2 else 0.02,
            )
            for role, count in sorted(clicked_counts.items())
        )
        price_band = None
        if price_values:
            price_band = f"₹{min(price_values):,.0f}–₹{max(price_values):,.0f}"
        return BehavioralIntentSummary(
            viewed_roles=tuple(sorted(viewed_roles)),
            clicked_roles=tuple(sorted(clicked_counts)),
            price_band_observed=price_band,
            proposition_interest=proposition_interest,
            role_interests=interests,
        )

    def latest_search_request_id(self, session: Session, session_id: str) -> str | None:
        return session.scalar(
            select(CommerceEvent.search_request_id)
            .where(
                CommerceEvent.session_id == session_id,
                CommerceEvent.event_type == CommerceEventType.SEARCH_SUBMITTED,
            )
            .order_by(CommerceEvent.occurred_at.desc())
            .limit(1)
        )

    def record_search(
        self, session: Session, *, session_id: str | None, search_request_id: str, intent: ShoppingIntent,
        parser_source: str, outcome: dict | None = None,
    ) -> None:
        if not session_id:
            return
        previous = self.latest_search_request_id(session, session_id)
        data = {"intent": safe_intent_snapshot(intent, parser_source), "outcome": outcome or {}}
        self._append(session, event_type=CommerceEventType.SEARCH_SUBMITTED, session_id=session_id,
                     search_request_id=search_request_id, source=CommerceEventSource.BACKEND, event_data=data)
        if previous:
            self._append(session, event_type=CommerceEventType.SEARCH_REFINED, session_id=session_id,
                         search_request_id=search_request_id, source=CommerceEventSource.BACKEND,
                         event_data={"previous_search_request_id": previous})
        session.commit()

    def record_backend(
        self, session: Session, *, event_type: CommerceEventType, session_id: str | None = None,
        search_request_id: str | None = None, product_id: int | None = None, proposition_id: str | None = None,
        order_id: int | None = None, event_data: dict | None = None,
    ) -> None:
        if order_id is not None:
            existing = session.scalar(
                select(CommerceEvent.id).where(
                    CommerceEvent.order_id == order_id, CommerceEvent.event_type == event_type
                )
            )
            if existing:
                return
        self._append(session, event_type=event_type, session_id=session_id, search_request_id=search_request_id,
                     product_id=product_id, proposition_id=proposition_id, order_id=order_id,
                     source=CommerceEventSource.BACKEND, event_data=event_data or {})
        session.commit()

    def record_client(self, session: Session, request: ClientCommerceEventRequest) -> bool:
        event_type = CommerceEventType(request.event_type)
        if event_type not in CLIENT_EVENT_TYPES:
            raise ValueError("This event type is backend-authoritative.")
        session_id = str(request.commerce_session_id)
        if session.get(CommerceSession, session_id) is None:
            raise ValueError("Commerce session was not found.")
        duplicate = session.scalar(
            select(CommerceEvent.id).where(
                CommerceEvent.session_id == session_id,
                CommerceEvent.client_event_id == str(request.event_id),
            )
        )
        if duplicate:
            return True
        if request.product_id is not None and session.get(Product, request.product_id) is None:
            raise ValueError("Product was not found.")
        if request.proposition_id is not None and session.get(CommercePropositionRecord, str(request.proposition_id)) is None:
            raise ValueError("Generated proposition was not found.")
        order = session.get(Order, request.order_id) if request.order_id is not None else None
        if request.order_id is not None and order is None:
            raise ValueError("Order was not found.")
        if request.search_request_id is not None:
            correlated_search = session.scalar(
                select(CommerceEvent.id).where(
                    CommerceEvent.session_id == session_id,
                    CommerceEvent.search_request_id == str(request.search_request_id),
                    CommerceEvent.event_type == CommerceEventType.SEARCH_SUBMITTED,
                )
            )
            if correlated_search is None:
                raise ValueError("Search request was not found for this commerce session.")
        if request.proposition_id is not None:
            correlated_proposition = session.scalar(
                select(CommerceEvent.id).where(
                    CommerceEvent.session_id == session_id,
                    CommerceEvent.search_request_id == str(request.search_request_id),
                    CommerceEvent.proposition_id == str(request.proposition_id),
                    CommerceEvent.event_type == CommerceEventType.PROPOSITION_GENERATED,
                )
            )
            if correlated_proposition is None:
                raise ValueError("Generated proposition was not found for this search request.")
            if order is not None:
                authoritative_proposition = self._proposition_for_order(session, order)
                if (
                    authoritative_proposition is None
                    or authoritative_proposition.id != str(request.proposition_id)
                ):
                    raise ValueError("Checkout event does not match the order.")
        self._append(
            session, event_type=event_type, session_id=session_id,
            search_request_id=str(request.search_request_id) if request.search_request_id else None,
            product_id=request.product_id, proposition_id=str(request.proposition_id) if request.proposition_id else None,
            order_id=request.order_id, source=CommerceEventSource.CLIENT, event_data={"metadata": request.metadata},
            client_event_id=str(request.event_id),
        )
        session.commit()
        return False

    def safe_record_backend(self, session: Session, **kwargs: object) -> None:
        try:
            self.record_backend(session, **kwargs)
        except (SQLAlchemyError, ValueError):
            session.rollback()

    def record_verified_payment(self, session: Session, order: Order, payment_attempt_id: int) -> None:
        attribution = session.scalar(
            select(PaymentCommerceAttribution).where(PaymentCommerceAttribution.order_id == order.id)
        )
        if attribution is None:
            attribution = self.ensure_payment_attribution(session, order, payment_attempt_id)
            session.commit()
        self.record_backend(
            session,
            event_type=CommerceEventType.PAYMENT_VERIFIED,
            session_id=attribution.session_id,
            search_request_id=attribution.search_request_id,
            proposition_id=attribution.proposition_id,
            order_id=order.id,
            event_data={"payment_attempt_id": payment_attempt_id, "mechanism": attribution.mechanism},
        )

    def ensure_payment_attribution(
        self, session: Session, order: Order, payment_attempt_id: int
    ) -> PaymentCommerceAttribution:
        existing = session.scalar(
            select(PaymentCommerceAttribution).where(PaymentCommerceAttribution.order_id == order.id)
        )
        if existing is not None:
            return existing
        proposition = self._proposition_for_order(session, order)
        generated = session.scalar(
            select(CommerceEvent)
            .where(
                CommerceEvent.proposition_id == proposition.id,
                CommerceEvent.event_type == CommerceEventType.PROPOSITION_GENERATED,
                CommerceEvent.source == CommerceEventSource.BACKEND,
            )
            .order_by(CommerceEvent.occurred_at.desc())
            .limit(1)
        ) if proposition is not None else None
        attribution = PaymentCommerceAttribution(
            order_id=order.id,
            payment_attempt_id=payment_attempt_id,
            proposition_id=proposition.id if proposition else None,
            session_id=order.commerce_session_id or (generated.session_id if generated else None),
            search_request_id=order.search_request_id or (generated.search_request_id if generated else None),
            mechanism=order.proposition_type,
        )
        session.add(attribution)
        return attribution

    @staticmethod
    def _proposition_for_order(
        session: Session, order: Order
    ) -> CommercePropositionRecord | None:
        prefix = "proposition:"
        if not order.proposition_reference.startswith(prefix):
            return None
        proposition = session.get(
            CommercePropositionRecord, order.proposition_reference.removeprefix(prefix)
        )
        if proposition is None or proposition.type != order.proposition_type:
            return None
        return proposition

    @staticmethod
    def _append(
        session: Session, *, event_type: CommerceEventType, session_id: str | None,
        source: CommerceEventSource, event_data: dict, search_request_id: str | None = None,
        product_id: int | None = None, proposition_id: str | None = None, order_id: int | None = None,
        client_event_id: str | None = None,
    ) -> CommerceEvent:
        event = CommerceEvent(
            id=str(uuid4()), session_id=session_id, event_type=event_type, search_request_id=search_request_id,
            product_id=product_id, proposition_id=proposition_id, order_id=order_id, event_data=event_data,
            source=source, schema_version="v1", client_event_id=client_event_id,
        )
        session.add(event)
        return event
