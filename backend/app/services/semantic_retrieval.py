"""Bounded, optional MiniLM discovery retrieval.

This module intentionally owns the sentence-transformers dependency and its
process-local cache.  Callers receive ranked product identifiers only; facts,
price, stock, exclusions, merchant policy and customer-facing evidence remain
outside this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import logging
from math import isfinite
from threading import RLock
from time import perf_counter
from typing import Any, Callable, Protocol

from app.domain.catalogue_facts import FactAssertion, FactFamily
from app.models.product import Product
from app.schemas.intent import (
    IntentRequirementProvenance,
    IntentSignalPolarity,
    ShoppingIntent,
)
from app.services.relevance import normalize_text


logger = logging.getLogger(__name__)

MINILM_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MINILM_EMBEDDING_DIMENSION = 384
MAX_SEMANTIC_QUERY_REPRESENTATIONS = 4


class SemanticRetrievalError(RuntimeError):
    """A non-authoritative retrieval failure that must use lexical fallback."""


@dataclass(frozen=True)
class SemanticDocument:
    product_id: int
    text: str


@dataclass(frozen=True)
class SemanticCandidate:
    product_id: int
    rank: int


@dataclass(frozen=True)
class SemanticRetrievalResult:
    candidates: tuple[SemanticCandidate, ...]
    query_count: int
    latency_ms: float


class SemanticRetriever(Protocol):
    model_id: str

    def prepare_catalogue(self, products: list[Product]) -> None:
        ...

    def retrieve(
        self, products: list[Product], intent: ShoppingIntent, candidate_k: int
    ) -> SemanticRetrievalResult:
        ...


@dataclass(frozen=True)
class _EmbeddingSnapshot:
    fingerprint: str
    product_ids: tuple[int, ...]
    vectors: Any


def semantic_document(product: Product) -> SemanticDocument:
    """Create a stable non-financial discovery document from approved fields."""

    concepts = sorted(
        {
            fact.concept.replace("-", " ")
            for fact in product.catalogue_facts
            if fact.assertion == FactAssertion.SUPPORTED.value
            and fact.family in {family.value for family in FactFamily}
        }
    )
    parts = [product.name, product.description, *concepts]
    return SemanticDocument(product_id=product.id, text=". ".join(part for part in parts if part))


_RECIPIENT_CONTEXT_TOKENS = frozenset({
    "mother", "mom", "mum", "father", "dad", "daddy", "parent", "her", "him",
    "friend", "partner", "wife", "husband",
})


def _semantic_raw_terms(intent: ShoppingIntent) -> list[str]:
    raw_terms = normalize_text(intent.raw_query).split()
    ignored = {
        *normalize_text(intent.recipient or "").split(),
        *normalize_text(intent.occasion or "").split(),
        *_RECIPIENT_CONTEXT_TOKENS,
    }
    if intent.gifting_intent:
        ignored.update({"gift", "gifting", "present", "birthday", "anniversary"})
    for signal in intent.hard_negative_signals:
        ignored.update(normalize_text(signal.value).split())
    canonical_terms = {
        token
        for role in intent.resolved_roles
        for token in normalize_text(role).split()
    }
    canonical_terms.update(
        token
        for requirement in intent.requirements
        if requirement.polarity == IntentSignalPolarity.POSITIVE
        and requirement.provenance != IntentRequirementProvenance.AMBIGUOUS
        for token in normalize_text(requirement.concept).split()
    )
    return [term for term in raw_terms if term not in ignored | canonical_terms]


def semantic_query_representations(
    intent: ShoppingIntent, *, strategy: str = "combined"
) -> tuple[str, ...]:
    """Return controlled, non-authoritative query representations."""

    cleaned_raw = " ".join(_semantic_raw_terms(intent))

    canonical_parts = [role.replace("-", " ") for role in intent.resolved_roles]
    canonical_parts.extend(
        requirement.concept.replace("-", " ")
        for requirement in intent.requirements
        if requirement.polarity == IntentSignalPolarity.POSITIVE
        and requirement.provenance != IntentRequirementProvenance.AMBIGUOUS
        and requirement.family != FactFamily.ROLE.value
    )
    canonical = " ".join(dict.fromkeys(canonical_parts))

    if strategy == "raw":
        base = [cleaned_raw]
    elif strategy == "canonical":
        base = [canonical]
    elif strategy == "combined":
        base = [" ".join(part for part in (canonical, cleaned_raw) if part)]
    else:
        raise ValueError(f"unsupported semantic query strategy: {strategy}")

    # Base query plus at most three role supplements is globally bounded.
    if len(intent.resolved_roles) > 1 and strategy != "raw":
        base.extend(role.replace("-", " ") for role in intent.resolved_roles[: MAX_SEMANTIC_QUERY_REPRESENTATIONS - 1])
    return tuple(dict.fromkeys(value for value in base if value.strip()))[:MAX_SEMANTIC_QUERY_REPRESENTATIONS]


class MiniLMSemanticRetriever:
    """Exact cosine scan over a bounded process-local MiniLM embedding cache.

    Embeddings are L2-normalised by sentence-transformers, therefore vector
    dot product is deliberate cosine similarity.  The repository pins the
    package version but model revisions are currently identified by model ID;
    deployment should bake/cache the selected model rather than downloading it
    during a request.
    """

    def __init__(
        self,
        model_id: str = MINILM_MODEL_ID,
        *,
        local_files_only: bool = True,
        query_strategy: str = "combined",
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._model_id = model_id
        self._local_files_only = local_files_only
        self._query_strategy = query_strategy
        self._model_factory = model_factory
        self._lock = RLock()
        self._model: Any | None = None
        self._snapshot: _EmbeddingSnapshot | None = None

    @property
    def model_id(self) -> str:
        return self._model_id

    def prepare_catalogue(self, products: list[Product]) -> None:
        """Warm/update the stable canonical catalogue snapshot once per change."""
        documents = tuple(semantic_document(product) for product in products)
        if not documents:
            return
        with self._lock:
            model = self._get_model()
            self._get_snapshot(model, documents)

    def retrieve(
        self, products: list[Product], intent: ShoppingIntent, candidate_k: int
    ) -> SemanticRetrievalResult:
        started = perf_counter()
        if candidate_k <= 0 or not products:
            return SemanticRetrievalResult((), 0, 0.0)
        queries = semantic_query_representations(intent, strategy=self._query_strategy)
        if not queries:
            return SemanticRetrievalResult((), 0, 0.0)
        try:
            with self._lock:
                model = self._get_model()
                snapshot = self._snapshot_for_eligible_products(model, products)
                query_vectors = model.encode(
                    list(queries),
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                ranked = self._rank(snapshot, query_vectors, candidate_k, {product.id for product in products})
        except SemanticRetrievalError:
            raise
        except Exception as error:  # encode/load/type errors remain non-fatal.
            raise SemanticRetrievalError(type(error).__name__) from error
        return SemanticRetrievalResult(
            candidates=ranked,
            query_count=len(queries),
            latency_ms=round((perf_counter() - started) * 1000, 3),
        )

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self._model_id != MINILM_MODEL_ID:
            raise SemanticRetrievalError("unsupported_model")
        try:
            factory = self._model_factory
            if factory is None:
                from sentence_transformers import SentenceTransformer

                factory = SentenceTransformer
            self._model = factory(
                self.model_id,
                device="cpu",
                local_files_only=self._local_files_only,
            )
            dimension_getter = getattr(
                self._model, "get_embedding_dimension", self._model.get_sentence_embedding_dimension
            )
            dimension = dimension_getter()
            if dimension != MINILM_EMBEDDING_DIMENSION:
                raise SemanticRetrievalError("unexpected_embedding_dimension")
            return self._model
        except SemanticRetrievalError:
            raise
        except Exception as error:
            raise SemanticRetrievalError(type(error).__name__) from error

    def _get_snapshot(self, model: Any, documents: tuple[SemanticDocument, ...]) -> _EmbeddingSnapshot:
        fingerprint = self._fingerprint(documents)
        if self._snapshot is not None and self._snapshot.fingerprint == fingerprint:
            return self._snapshot
        try:
            vectors = model.encode(
                [document.text for document in documents],
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            if getattr(vectors, "ndim", None) != 2 or vectors.shape[0] != len(documents):
                raise SemanticRetrievalError("malformed_document_embeddings")
            self._snapshot = _EmbeddingSnapshot(
                fingerprint=fingerprint,
                product_ids=tuple(document.product_id for document in documents),
                vectors=vectors,
            )
            return self._snapshot
        except SemanticRetrievalError:
            raise
        except Exception as error:
            raise SemanticRetrievalError(type(error).__name__) from error

    def _snapshot_for_eligible_products(
        self, model: Any, products: list[Product]
    ) -> _EmbeddingSnapshot:
        eligible_ids = {product.id for product in products}
        if self._snapshot is not None and eligible_ids.issubset(self._snapshot.product_ids):
            return self._snapshot
        # Standalone callers/tests that did not prepare the canonical catalogue
        # remain safe; normal search always prepares the full catalogue first.
        return self._get_snapshot(model, tuple(semantic_document(product) for product in products))

    def _rank(
        self, snapshot: _EmbeddingSnapshot, query_vectors: Any, candidate_k: int,
        eligible_ids: set[int],
    ) -> tuple[SemanticCandidate, ...]:
        if getattr(query_vectors, "ndim", None) != 2 or query_vectors.shape[1] != snapshot.vectors.shape[1]:
            raise SemanticRetrievalError("malformed_query_embeddings")
        fused: dict[int, float] = {}
        per_query_k = max(1, candidate_k // max(1, len(query_vectors)))
        for query_vector in query_vectors:
            scores = snapshot.vectors @ query_vector
            if len(scores) != len(snapshot.product_ids):
                raise SemanticRetrievalError("malformed_similarity_scores")
            if not all(isfinite(float(score)) for score in scores):
                raise SemanticRetrievalError("non_finite_similarity_score")
            ordered = [
                index for index in sorted(
                    range(len(scores)),
                    key=lambda index: (-float(scores[index]), snapshot.product_ids[index]),
                ) if snapshot.product_ids[index] in eligible_ids
            ][:per_query_k]
            for rank, index in enumerate(ordered, start=1):
                product_id = snapshot.product_ids[index]
                fused[product_id] = fused.get(product_id, 0.0) + 1 / (60 + rank)
        return tuple(
            SemanticCandidate(product_id=product_id, rank=rank)
            for rank, (product_id, _) in enumerate(
                sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:candidate_k], start=1
            )
        )

    def _fingerprint(self, documents: tuple[SemanticDocument, ...]) -> str:
        digest = sha256()
        digest.update(self._model_id.encode("utf-8"))
        digest.update(b"\0normalised-cosine-v1\0")
        for document in documents:
            digest.update(f"{document.product_id}\0{document.text}\0".encode("utf-8"))
        return digest.hexdigest()
