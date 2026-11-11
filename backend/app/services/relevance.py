"""Generic, deterministic relevance primitives for commerce search.

This layer deliberately knows nothing about payments, merchant economics, or
category-specific product concepts. It receives only pre-eligible products and
returns relevance provenance; the final ConstraintEngine remains authoritative.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from math import log
import re
import unicodedata
from typing import Protocol

from app.models.product import Product
from app.schemas.intent import ShoppingIntent


_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "for", "from", "give",
    "i", "in", "is", "it", "like", "me", "my", "of", "on", "or", "please",
    "something", "the", "to", "with", "would",
}


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def tokenize(value: str) -> list[str]:
    return [
        token
        for token in normalize_text(value).split()
        if len(token) > 1 and token not in _STOP_WORDS and not token.isdigit()
    ]


@dataclass(frozen=True)
class SearchDocument:
    id: int
    title: str
    description: str
    category: str
    searchable_text: str
    aliases: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    attributes: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)


class ProductSearchDocumentAdapter:
    """Maps canonical products to a non-financial search representation."""

    def from_product(self, product: Product) -> SearchDocument:
        tags = tuple(product.tags)
        searchable_text = " ".join(
            [product.name, product.description, product.category, *tags]
        )
        return SearchDocument(
            id=product.id,
            title=product.name,
            description=product.description,
            category=product.category,
            searchable_text=searchable_text,
            tags=tags,
            attributes={tag: "true" for tag in tags},
            metadata={"category": product.category},
        )


@dataclass(frozen=True)
class RelevanceQuery:
    raw_query: str
    terms: tuple[str, ...]
    repaired_terms: tuple[str, ...]


@dataclass(frozen=True)
class RelevanceCandidate:
    product: Product
    document: SearchDocument
    lexical_score: float
    fuzzy_score: float
    character_score: float
    phrase_score: float
    fused_score: float
    matched_terms: tuple[str, ...]
    repaired_terms: tuple[str, ...]
    semantic_rank: int | None = None
    retrieval_prior: float | None = None
    retrieval_mode: str = "deterministic"


class RelevanceAdapter(Protocol):
    def index(self, products: list[Product]) -> list[SearchDocument]:
        ...

    def retrieve(
        self, products: list[Product], intent: ShoppingIntent
    ) -> list[RelevanceCandidate]:
        ...


class DeterministicRelevanceAdapter:
    """BM25-style, phrase, fuzzy and character fallback retrieval.

    The bounded RRF fusion keeps component score scales independent and exposes
    ranks/scores through `RelevanceCandidate` for evaluation and debugging.
    """

    def __init__(self, adapter: ProductSearchDocumentAdapter | None = None) -> None:
        self._document_adapter = adapter or ProductSearchDocumentAdapter()

    def index(self, products: list[Product]) -> list[SearchDocument]:
        return [self._document_adapter.from_product(product) for product in products]

    def retrieve(
        self, products: list[Product], intent: ShoppingIntent
    ) -> list[RelevanceCandidate]:
        documents = self.index(products)
        query = self._build_query(intent, documents)
        if not documents:
            return []

        document_tokens = [tokenize(document.searchable_text) for document in documents]
        average_length = sum(len(tokens) for tokens in document_tokens) / len(documents)
        document_frequency = Counter(
            term for tokens in document_tokens for term in set(tokens)
        )
        raw: list[dict[str, object]] = []
        for product, document, tokens in zip(products, documents, document_tokens, strict=True):
            lexical = self._bm25(query.terms, tokens, document_frequency, len(documents), average_length)
            title_lexical = self._bm25(
                query.terms,
                tokenize(document.title),
                document_frequency,
                len(documents),
                average_length,
            )
            lexical += title_lexical * 1.8
            normalized_document = normalize_text(document.searchable_text)
            normalized_query = normalize_text(intent.raw_query)
            phrase = 1.0 if normalized_query and normalized_query in normalized_document else 0.0
            matched = tuple(sorted(set(query.terms).intersection(tokens)))
            fuzzy = self._fuzzy(query.terms, document)
            character = self._character_similarity(query.terms, tokens)
            raw.append(
                {
                    "product": product,
                    "document": document,
                    "lexical": lexical,
                    "fuzzy": fuzzy,
                    "character": character,
                    "phrase": phrase,
                    "matched": matched,
                }
            )

        lexical_ranks = self._ranks(raw, "lexical")
        fuzzy_ranks = self._ranks(raw, "fuzzy")
        character_ranks = self._ranks(raw, "character")
        candidates: list[RelevanceCandidate] = []
        for item in raw:
            product = item["product"]
            assert isinstance(product, Product)
            document = item["document"]
            assert isinstance(document, SearchDocument)
            identifier = product.id
            rrf = sum(
                1 / (60 + rank)
                for rank in (lexical_ranks[identifier], fuzzy_ranks[identifier], character_ranks[identifier])
            )
            phrase = float(item["phrase"])
            fused = min(1.0, rrf * 22 + phrase * 0.25)
            candidates.append(
                RelevanceCandidate(
                    product=product,
                    document=document,
                    lexical_score=float(item["lexical"]),
                    fuzzy_score=float(item["fuzzy"]),
                    character_score=float(item["character"]),
                    phrase_score=phrase,
                    fused_score=fused,
                    matched_terms=item["matched"],  # type: ignore[arg-type]
                    repaired_terms=query.repaired_terms,
                )
            )
        return sorted(candidates, key=lambda item: (-item.fused_score, item.product.price, item.product.name))

    def _build_query(self, intent: ShoppingIntent, documents: list[SearchDocument]) -> RelevanceQuery:
        terms = tokenize(intent.raw_query)
        vocabulary = {
            term for document in documents for term in tokenize(document.searchable_text) if len(term) >= 3
        }
        repaired: list[str] = []
        expanded: list[str] = []
        for term in terms:
            if term in vocabulary:
                expanded.append(term)
                continue
            segments = self._segment_compact_term(term, vocabulary)
            if segments:
                expanded.extend(segments)
                repaired.append(term)
            else:
                expanded.append(term)
        for concept in intent.product_concepts:
            expanded.extend(tokenize(concept.value))
        for attribute in intent.desired_attributes:
            expanded.extend(tokenize(attribute.value))
        return RelevanceQuery(intent.raw_query, tuple(dict.fromkeys(expanded)), tuple(repaired))

    @staticmethod
    def _bm25(
        terms: tuple[str, ...],
        tokens: list[str],
        frequency: Counter[str],
        corpus_size: int,
        average_length: float,
    ) -> float:
        if not tokens or not terms:
            return 0.0
        counts = Counter(tokens)
        score = 0.0
        for term in terms:
            if term not in counts:
                continue
            idf = log(1 + (corpus_size - frequency[term] + 0.5) / (frequency[term] + 0.5))
            denominator = counts[term] + 1.5 * (1 - 0.75 + 0.75 * len(tokens) / max(1, average_length))
            score += idf * counts[term] * 2.5 / denominator
        return score

    @staticmethod
    def _fuzzy(terms: tuple[str, ...], document: SearchDocument) -> float:
        choices = tokenize(" ".join([document.title, *document.aliases, *document.tags]))
        if not terms or not choices:
            return 0.0
        return sum(
            max(SequenceMatcher(None, term, choice).ratio() for choice in choices)
            for term in terms
        ) / len(terms)

    @staticmethod
    def _character_similarity(terms: tuple[str, ...], tokens: list[str]) -> float:
        left = _trigrams(" ".join(terms))
        right = _trigrams(" ".join(tokens))
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    @staticmethod
    def _segment_compact_term(term: str, vocabulary: set[str]) -> tuple[str, ...] | None:
        if len(term) < 8 or not term.isascii():
            return None
        best: list[tuple[int, tuple[str, ...] | None]] = [(0, ())] + [(-10_000, None)] * len(term)
        for end in range(1, len(term) + 1):
            for start in range(max(0, end - 20), end):
                fragment = term[start:end]
                if len(fragment) < 3 or fragment not in vocabulary or best[start][1] is None:
                    continue
                score = best[start][0] + len(fragment) ** 2
                if score > best[end][0]:
                    best[end] = (score, (*best[start][1], fragment))
        segments = best[-1][1]
        return segments if segments and len(segments) > 1 else None

    @staticmethod
    def _ranks(items: list[dict[str, object]], key: str) -> dict[int, int]:
        ordered = sorted(
            items,
            key=lambda item: (-float(item[key]), int(getattr(item["product"], "id"))),
        )
        return {getattr(item["product"], "id"): index + 1 for index, item in enumerate(ordered)}


@dataclass(frozen=True)
class RetrievalDiagnostics:
    mode: str
    lexical_candidate_count: int
    semantic_candidate_count: int
    fused_candidate_count: int
    semantic_latency_ms: float = 0.0
    fallback_reason: str | None = None


class HybridRelevanceAdapter:
    """Fuses bounded lexical and semantic discovery lists with rank-only RRF.

    The lexical adapter remains the compatibility path. Semantic failures are
    deliberately absorbed here so a local model/cache problem cannot make a
    shopper search fail. The caller's final ConstraintEngine gate still runs
    after this adapter.
    """

    _LEXICAL_CANDIDATE_K = 12

    def __init__(
        self,
        *,
        enabled: bool,
        semantic_retriever: object | None = None,
        semantic_candidate_k: int = 12,
        lexical_candidate_k: int = _LEXICAL_CANDIDATE_K,
        lexical: DeterministicRelevanceAdapter | None = None,
    ) -> None:
        self._enabled = enabled
        self._semantic_retriever = semantic_retriever
        self._semantic_candidate_k = semantic_candidate_k
        self._lexical_candidate_k = lexical_candidate_k
        self._lexical = lexical or DeterministicRelevanceAdapter()
        self.diagnostics = RetrievalDiagnostics("deterministic", 0, 0, 0)

    @classmethod
    def from_settings(cls) -> "HybridRelevanceAdapter":
        from app.core.config import get_settings
        from app.services.semantic_retrieval import MiniLMSemanticRetriever

        settings = get_settings()
        return cls(
            enabled=settings.semantic_retrieval_enabled,
            semantic_retriever=MiniLMSemanticRetriever(
                settings.semantic_retrieval_model,
                local_files_only=settings.semantic_retrieval_local_files_only,
            ) if settings.semantic_retrieval_enabled else None,
            semantic_candidate_k=settings.semantic_retrieval_candidate_k,
        )

    def index(self, products: list[Product]) -> list[SearchDocument]:
        return self._lexical.index(products)

    def retrieve(
        self, products: list[Product], intent: ShoppingIntent
    ) -> list[RelevanceCandidate]:
        lexical_all = self._lexical.retrieve(products, intent)
        if not self._enabled:
            self.diagnostics = RetrievalDiagnostics(
                "deterministic", len(lexical_all), 0, len(lexical_all)
            )
            return lexical_all
        if self._semantic_retriever is None:
            return self._fallback(lexical_all, "not_configured")

        try:
            semantic = self._semantic_retriever.retrieve(
                products, intent, self._semantic_candidate_k
            )
            semantic_candidates = tuple(semantic.candidates)
            if not semantic_candidates:
                return self._fallback(lexical_all, "empty_result")
            if any(
                candidate.product_id not in {product.id for product in products}
                or candidate.rank <= 0
                for candidate in semantic_candidates
            ):
                return self._fallback(lexical_all, "malformed_result")
        except Exception as error:
            # The semantic implementation intentionally does not receive a raw
            # query in logs. Error category is enough for local diagnostics.
            return self._fallback(lexical_all, type(error).__name__)

        lexical_ranked = lexical_all[: self._lexical_candidate_k]
        lexical_by_id = {candidate.product.id: candidate for candidate in lexical_all}
        lexical_ranks = {
            candidate.product.id: rank
            for rank, candidate in enumerate(lexical_ranked, start=1)
        }
        semantic_ranks = {
            candidate.product_id: candidate.rank for candidate in semantic_candidates
        }
        identifiers = set(lexical_ranks) | set(semantic_ranks)
        fused: list[RelevanceCandidate] = []
        for identifier in identifiers:
            candidate = lexical_by_id[identifier]
            rrf = sum(
                1 / (60 + rank)
                for rank in (lexical_ranks.get(identifier), semantic_ranks.get(identifier))
                if rank is not None
            )
            fused.append(
                RelevanceCandidate(
                    product=candidate.product,
                    document=candidate.document,
                    lexical_score=candidate.lexical_score,
                    fuzzy_score=candidate.fuzzy_score,
                    character_score=candidate.character_score,
                    phrase_score=candidate.phrase_score,
                    fused_score=min(1.0, rrf * 30),
                    matched_terms=candidate.matched_terms,
                    repaired_terms=candidate.repaired_terms,
                    semantic_rank=semantic_ranks.get(identifier),
                    retrieval_mode="hybrid",
                )
            )
        result = sorted(
            fused,
            key=lambda candidate: (-candidate.fused_score, candidate.product.price, candidate.product.name),
        )
        self.diagnostics = RetrievalDiagnostics(
            "hybrid",
            len(lexical_ranked),
            len(semantic_candidates),
            len(result),
            semantic_latency_ms=semantic.latency_ms,
        )
        return result

    def _fallback(
        self, lexical: list[RelevanceCandidate], reason: str,
    ) -> list[RelevanceCandidate]:
        self.diagnostics = RetrievalDiagnostics(
            "deterministic_fallback", len(lexical), 0, len(lexical), fallback_reason=reason
        )
        return lexical


class SemanticPrimaryRelevanceAdapter:
    """Semantic-primary discovery with a deliberately narrow exact-title lane.

    The historical equal-RRF adapter remains above for development comparison,
    but is not selected by runtime. Generic lexical/BM25 overlap receives no
    authority: only an exact normalized product title can be protected.
    """

    def __init__(
        self, *, enabled: bool, semantic_retriever: object | None = None,
                 semantic_candidate_k: int = 12,
                 lexical: DeterministicRelevanceAdapter | None = None) -> None:
        self._enabled = enabled
        self._semantic_retriever = semantic_retriever
        self._semantic_candidate_k = semantic_candidate_k
        self._lexical = lexical or DeterministicRelevanceAdapter()
        self.diagnostics = RetrievalDiagnostics("deterministic", 0, 0, 0)

    @classmethod
    def from_settings(cls) -> "SemanticPrimaryRelevanceAdapter":
        from app.core.config import get_settings
        from app.services.semantic_retrieval import MiniLMSemanticRetriever

        settings = get_settings()
        return cls(
            enabled=settings.semantic_retrieval_enabled,
            semantic_retriever=MiniLMSemanticRetriever(
                settings.semantic_retrieval_model,
                local_files_only=settings.semantic_retrieval_local_files_only,
            ) if settings.semantic_retrieval_enabled else None,
            semantic_candidate_k=settings.semantic_retrieval_candidate_k,
        )

    def prepare_catalogue(self, products: list[Product]) -> None:
        if not self._enabled or self._semantic_retriever is None:
            return
        try:
            self._semantic_retriever.prepare_catalogue(products)
        except Exception as error:
            self.diagnostics = RetrievalDiagnostics(
                "deterministic_fallback", 0, 0, 0, fallback_reason=type(error).__name__
            )

    def index(self, products: list[Product]) -> list[SearchDocument]:
        return self._lexical.index(products)

    def retrieve(self, products: list[Product], intent: ShoppingIntent) -> list[RelevanceCandidate]:
        lexical_all = self._lexical.retrieve(products, intent)
        if not self._enabled:
            self.diagnostics = RetrievalDiagnostics("deterministic", len(lexical_all), 0, len(lexical_all))
            return lexical_all
        if self._semantic_retriever is None:
            return self._fallback(lexical_all, "not_configured")
        try:
            semantic = self._semantic_retriever.retrieve(products, intent, self._semantic_candidate_k)
            semantic_candidates = tuple(semantic.candidates)
            identifiers = {product.id for product in products}
            if not semantic_candidates:
                return self._fallback(lexical_all, "empty_result")
            if any(candidate.product_id not in identifiers or candidate.rank <= 0 for candidate in semantic_candidates):
                return self._fallback(lexical_all, "malformed_result")
        except Exception as error:
            return self._fallback(lexical_all, type(error).__name__)

        lexical_by_id = {candidate.product.id: candidate for candidate in lexical_all}
        semantic_ids = [candidate.product_id for candidate in semantic_candidates]
        semantic_ranks = {candidate.product_id: candidate.rank for candidate in semantic_candidates}
        exact = self._exact_title_candidate(intent, lexical_all)
        ordered_ids = ([exact.product.id] if exact else []) + [
            identifier for identifier in semantic_ids if exact is None or identifier != exact.product.id
        ]
        denominator = max(1, len(ordered_ids))
        result: list[RelevanceCandidate] = []
        for rank, identifier in enumerate(ordered_ids, start=1):
            candidate = lexical_by_id[identifier]
            prior = 1.0 if exact and identifier == exact.product.id else (denominator - rank + 1) / denominator
            result.append(RelevanceCandidate(
                product=candidate.product, document=candidate.document,
                lexical_score=candidate.lexical_score, fuzzy_score=candidate.fuzzy_score,
                character_score=candidate.character_score, phrase_score=candidate.phrase_score,
                fused_score=prior, matched_terms=candidate.matched_terms,
                repaired_terms=candidate.repaired_terms,
                semantic_rank=semantic_ranks.get(identifier),
                retrieval_prior=prior, retrieval_mode="semantic_primary",
            ))
        self.diagnostics = RetrievalDiagnostics(
            "semantic_primary", 1 if exact else 0, len(semantic_candidates), len(result),
            semantic_latency_ms=semantic.latency_ms,
        )
        return result

    @staticmethod
    def _exact_title_candidate(intent: ShoppingIntent, candidates: list[RelevanceCandidate]) -> RelevanceCandidate | None:
        query = normalize_text(intent.raw_query)
        exact = [candidate for candidate in candidates if normalize_text(candidate.product.name) == query]
        return sorted(exact, key=lambda candidate: (candidate.product.price, candidate.product.name))[0] if exact else None

    def _fallback(self, lexical: list[RelevanceCandidate], reason: str) -> list[RelevanceCandidate]:
        self.diagnostics = RetrievalDiagnostics("deterministic_fallback", len(lexical), 0, len(lexical), fallback_reason=reason)
        return lexical


def _trigrams(value: str) -> set[str]:
    padded = f"  {normalize_text(value)}  "
    return {padded[index : index + 3] for index in range(max(0, len(padded) - 2))}
