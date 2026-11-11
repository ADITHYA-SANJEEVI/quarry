"""Versioned, transitively immutable process-boundary records."""
from dataclasses import dataclass

VERSION = "hpc_compute_v1"


@dataclass(frozen=True, slots=True)
class FactSnapshot:
    family: str
    concept: str
    assertion: str
    provenance: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class DocumentSnapshot:
    id: int
    title: str
    description: str
    category: str
    searchable_text: str
    aliases: tuple[str, ...]
    tags: tuple[str, ...]
    attributes: tuple[tuple[str, str], ...]
    metadata: tuple[tuple[str, str], ...]

    def document(self):
        from app.services.relevance import SearchDocument
        return SearchDocument(self.id, self.title, self.description, self.category,
                              self.searchable_text, self.aliases, self.tags,
                              dict(self.attributes), dict(self.metadata))


@dataclass(frozen=True, slots=True)
class ProductSnapshot:
    id: int
    ordinal: int
    name: str
    description: str
    category: str
    price: str
    stock: int
    active: bool
    archived_at: str | None
    tags: tuple[str, ...]
    facts: tuple[FactSnapshot, ...]
    document: DocumentSnapshot


@dataclass(frozen=True, slots=True)
class CatalogueSnapshot:
    generation: str
    products: tuple[ProductSnapshot, ...]
    version: str = VERSION


@dataclass(frozen=True, slots=True)
class IntentSnapshot:
    canonical_json: str
    version: str = VERSION


@dataclass(frozen=True, slots=True)
class BehaviorSnapshot:
    canonical_json: str
    version: str = VERSION


@dataclass(frozen=True, slots=True)
class QueryCorpusContext:
    generation: str
    context_id: str
    ordinals: tuple[int, ...]
    raw_query: str
    terms: tuple[str, ...]
    repaired_terms: tuple[str, ...]
    vocabulary: tuple[str, ...]
    n: int
    average_length: float
    document_frequency: tuple[tuple[str, int], ...]
    intent: IntentSnapshot
    behavior: BehaviorSnapshot
    version: str = VERSION


@dataclass(frozen=True, slots=True)
class FeatureRow:
    ordinal: int
    product_id: int
    lexical: float
    fuzzy: float
    character: float
    phrase: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FeatureTask:
    request_id: str
    partition: int
    generation: str
    context: QueryCorpusContext
    ordinals: tuple[int, ...]
    submitted_ns: int
    version: str = VERSION
    instrument: bool = False


@dataclass(frozen=True, slots=True)
class FeatureBatch:
    request_id: str
    partition: int
    generation: str
    context_id: str
    rows: tuple[FeatureRow, ...]
    pid: int
    dispatch_queue_ns: int
    execution_ns: int
    version: str = VERSION
    started_ns: int = 0
    ended_ns: int = 0
    cpu_ns: int = 0
    rss_bytes: int = 0
    result_serialized_bytes: int = 0
    result_serialization_ns: int = 0


class ComputeError(RuntimeError):
    """Safe category-only failures; no credentials/provider inputs in messages."""
