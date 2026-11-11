from dataclasses import asdict
from hashlib import sha256
import json

from app.services.hpc.contracts import (
    BehaviorSnapshot, CatalogueSnapshot, DocumentSnapshot, FactSnapshot,
    IntentSnapshot, ProductSnapshot, VERSION,
)


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def capture_catalogue(products, document_adapter=None) -> CatalogueSnapshot:
    from app.services.relevance import ProductSearchDocumentAdapter
    adapter = document_adapter or ProductSearchDocumentAdapter()
    records, facts, tuples, pairs = [], {}, {}, {}
    def shared_tuple(values):
        value = tuple(values)
        return tuples.setdefault(value, value)
    def shared_pair(value):
        return pairs.setdefault(value, value)
    for ordinal, product in enumerate(products):
        document = adapter.from_product(product)
        product_facts = []
        for fact in product.catalogue_facts:
            key = (fact.family, fact.concept, fact.assertion, fact.provenance, fact.schema_version)
            if key not in facts:
                facts[key] = FactSnapshot(*key)
            product_facts.append(facts[key])
        records.append(ProductSnapshot(
            product.id, ordinal, product.name, product.description, product.category,
            str(product.price), product.stock, product.catalogue_active,
            product.archived_at.isoformat() if product.archived_at else None,
            tuple(product.tags), shared_tuple(product_facts),
            DocumentSnapshot(document.id, document.title, document.description,
                document.category, document.searchable_text, tuple(document.aliases),
                tuple(document.tags), shared_tuple(shared_pair(p) for p in document.attributes.items()),
                shared_tuple(shared_pair(p) for p in document.metadata.items())),
        ))
    if len({p.id for p in records}) != len(records):
        raise ValueError("duplicate_product_ids")
    # Same canonical bytes as the original monolithic JSON, without retaining
    # a second complete 50K catalogue and a giant JSON string while hashing.
    digest = sha256(b'{"products":[')
    for index, record in enumerate(records):
        if index:
            digest.update(b',')
        digest.update(canonical_json(asdict(record)).encode())
    digest.update(b'],"version":' + canonical_json(VERSION).encode() + b'}')
    generation = digest.hexdigest()
    return CatalogueSnapshot(generation, tuple(records))


def capture_intent(intent) -> IntentSnapshot:
    return IntentSnapshot(canonical_json(intent.model_dump(mode="json")))


def capture_behavior(behavior=None) -> BehaviorSnapshot:
    from app.services.commerce_events import BehavioralIntentSummary
    return BehaviorSnapshot(canonical_json(asdict(behavior or BehavioralIntentSummary())))
