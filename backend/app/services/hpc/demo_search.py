"""Side-effect-free demo search using the production gates/ranker/compiler.

Only compiler persistence is replaced by an immutable preview, so synthetic
products never become live propositions. Live merchant offers are not read.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal
from hashlib import sha256
from time import perf_counter

from app.services.commerce_compiler import CommerceCompiler, CompilerState
from app.services.commerce_decision import build_commerce_decision
from app.services.commerce_ranker import CommerceRanker
from app.services.hpc.integration import ExecutionSearchService
from app.services.hpc.snapshot import canonical_json
from app.services.relevance import DeterministicRelevanceAdapter
from app.services.search_service import CatalogueSearchService


class ProfiledRanker(CommerceRanker):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.all_rows = ()

    def rank(self, *args, **kwargs):
        started = perf_counter()
        result = super().rank(*args, **kwargs)
        self.calls.append({"limit": kwargs.get("limit", 3), "ms": (perf_counter() - started) * 1000,
                           "input_count": len(args[0]), "output_count": len(result)})
        if kwargs.get("limit", 3) is None:
            self.all_rows = result
        return result


@dataclass(frozen=True)
class PreviewProposition:
    id: str
    title: str
    product_ids: tuple[int, ...]
    total: str
    evidence: tuple


class PreviewCompiler(CommerceCompiler):
    def _persist(self, session, intent, products, evidence):
        identity = sha256(canonical_json([intent.model_dump(mode="json"), [p.id for p in products]]).encode()).hexdigest()[:24]
        return PreviewProposition("demo-preview:" + identity, self._title(intent, products),
                                  tuple(p.id for p in products), str(sum((p.price for p in products), Decimal("0.00"))),
                                  tuple(asdict(e) for e in evidence))


def downstream(result, intent):
    compiled = PreviewCompiler().compile(None, intent=intent, ranked=result.recommendations,
                                        eligible_products=result.eligible_products, adequate=result.adequacy.adequate)
    preview = compiled.proposition
    decision = build_commerce_decision(clarification_needed=intent.clarification_needed,
        catalogue_adequate=result.adequacy.adequate, recommendation_count=len(result.recommendations),
        no_match=not result.eligible_products, compiler_state=compiled.state, compiler_reason_code=compiled.reason_code,
        merchant_offer_proposition_id=None, generated_proposition_id=preview.id if preview else None)
    return {"compiler_state": str(compiled.state), "compiler_reason": compiled.reason_code,
            "preview": asdict(preview) if preview else None, "decision": decision.model_dump(mode="json"),
            "merchant_offer_scope": "none_in_isolated_demo", "checkout_supported": False}


def make_service(catalogue, execution=None, runtime=None):
    ranker = ProfiledRanker()
    if execution is None:
        return CatalogueSearchService(retriever=catalogue, relevance=DeterministicRelevanceAdapter(), ranker=ranker), ranker
    return ExecutionSearchService(execution, runtime=runtime, retriever=catalogue, immutable_state=catalogue,
                                  relevance=DeterministicRelevanceAdapter(), ranker=ranker), ranker


def result_digest(result, ranker, decisions):
    """All ordered candidates, float bits, evidence, adequacy and decisions."""
    digest = sha256()
    def feed(value):
        digest.update(canonical_json(value).encode()); digest.update(b"\n")
    for label, rows in (("visible", result.recommendations), ("unbounded", ranker.all_rows)):
        feed(label)
        for r in rows:
            feed([r.product.id, float(r.score).hex(), [asdict(e) for e in r.evidence],
                  asdict(r.decomposition), r.reason, r.presentation_label])
    feed([p.id for p in result.eligible_products])
    feed(asdict(result.adequacy)); feed(result.filtered_counts_by_reason); feed(decisions)
    return digest.hexdigest()


def search_catalogue(catalogue, intent, execution, runtime):
    started = perf_counter()
    service, ranker = make_service(catalogue, execution, runtime)
    from app.services.application_observation import observe_service
    observe_service(service, ranker)
    result = service.search(None, intent)
    decision_started = perf_counter()
    decisions = downstream(result, intent)
    if decisions["preview"]:
        decisions["preview"]["product_ids"] = [f"demo:{catalogue.manifest['dataset_id']}:{i}" for i in decisions["preview"]["product_ids"]]
    return {"schema_version": "hpc_catalogue_search_v1", "dataset": catalogue.manifest,
            "intent": intent.model_dump(mode="json"), "recommendations": [
                {"product": catalogue.wire(r.product), "score": r.score, "score_hex": float(r.score).hex(),
                 "evidence": [asdict(e) for e in r.evidence], "decomposition": asdict(r.decomposition),
                 "reason": r.reason, "label": r.presentation_label} for r in result.recommendations],
            "adequacy": asdict(result.adequacy), "eligible_count": len(result.eligible_products),
            "discovery_count": result.discovery_count, "filtered_counts": result.filtered_counts_by_reason,
            "commerce": decisions, "execution": service.compute.metadata.model_dump(mode="json"),
            "profile": {"commerce_calls": ranker.calls, "downstream_ms": (perf_counter() - decision_started) * 1000,
                        "total_ms": (perf_counter() - started) * 1000}}
