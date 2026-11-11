"""Authored, versioned synthetic-catalogue judgments, independent of ranking.

Each rubric assigns every document a grade from its canonical facts. It is a
controlled synthetic benchmark, not human relevance judgments or a claim of
general shopping quality. Labels are materialized and hashed before search.
"""
from dataclasses import asdict
from hashlib import sha256
from math import log2
from uuid import uuid4

from app.schemas.hpc import SearchExecutionRequest
from app.services.hpc.demo_search import make_service, downstream, result_digest
from app.services.hpc.experiment_store import now
from app.services.hpc.snapshot import canonical_json
from app.services.intent_parser import FallbackIntentParser

JUDGMENTS = [
    {"id": "lexical-headphones", "query": "wireless headphones under 3000", "roles": ["headphones"], "attributes": ["wireless"], "max_price": "3000"},
    {"id": "compatible-charger", "query": "usb c charger under 1000", "roles": ["charger"], "attributes": ["usb-c"], "max_price": "1000"},
    {"id": "gentle-cleanser", "query": "gentle cleanser under 1000", "roles": ["cleanser"], "attributes": ["gentle"], "max_price": "1000"},
    {"id": "composition", "query": "gentle cleanser and fragrance-free moisturiser under 1500", "roles": ["cleanser", "moisturiser"],
     "attributes": ["gentle", "fragrance-free"], "max_price": "1500"},
    {"id":"power-preference", "query":"65 watt usb c charger under 1000", "roles":["charger"],
     "attributes":["usb-c"], "specifications_match":{"power_w":"65"}, "max_price":"1000"},
]


def relevance_grade(product, rubric):
    supported = {(f.family, f.concept) for f in product.catalogue_facts if f.assertion == "supported"}
    role = any(("role", role) in supported for role in rubric["roles"])
    if not role:
        return 0
    attributes = {concept for family, concept in supported if family != "role"}
    attribute_match = any(a in attributes for a in rubric["attributes"])
    title_match = all(term in product.name.lower().split() for term in rubric.get("title_terms",[]))
    specifications = dict(product.specifications)
    specification_match = all(specifications.get(key) == value for key,value in rubric.get("specifications_match",{}).items())
    return 3 if attribute_match and title_match and specification_match else 1


def ranking_metrics(ranked_ids, labels, k):
    grades = [labels.get(i, 0) for i in ranked_ids]
    reciprocal = next((1/(index+1) for index, grade in enumerate(grades) if grade > 0), 0.0)
    dcg = sum((2**g-1)/log2(i+2) for i,g in enumerate(grades[:k]))
    ideal = sum((2**g-1)/log2(i+2) for i,g in enumerate(sorted(labels.values(), reverse=True)[:k]))
    return {"mrr": reciprocal, "ndcg_at_k": dcg/ideal if ideal else None, "k": k,
            "relevant_documents": sum(g > 0 for g in labels.values()), "no_relevant_documents": ideal == 0}


def independent_eligible(products, intent):
    """Fact oracle for the generated catalogues; no ConstraintEngine calls.

    Synthetic data has complete, unique supported facts without contradictory
    assertions. Refuse unsupported input rather than silently using tag fallbacks.
    """
    result = []
    for p in products:
        if not p.catalogue_facts or any(f.assertion != "supported" for f in p.catalogue_facts):
            raise ValueError("quality_oracle_requires_complete_supported_synthetic_facts")
        facts = {(f.family, f.concept) for f in p.catalogue_facts}
        if not p.is_active or p.archived_at is not None or p.stock <= 0:
            continue
        if intent.strict_min_price is not None and p.price < intent.strict_min_price:
            continue
        if intent.strict_max_price is not None and p.price > intent.strict_max_price:
            continue
        if intent.resolved_roles and not any(("role",r) in facts for r in intent.resolved_roles):
            continue
        if any((family,s.value) in facts for s in intent.hard_negative_signals
               for family in ("attribute","suitability","use_case","compatibility")):
            continue
        required = [s for s in intent.compatibility_requirements if str(s.enforcement) == "hard"]
        if any((("compatibility",s.value) in facts) != (str(s.polarity) == "positive") for s in required):
            continue
        result.append(p.id)
    return result


def evaluate(catalogue, runtime, workers, k, source):
    document = {"schema_version": "hpc_quality_v1", "id": str(uuid4()), "created_at": now(),
                "dataset_id": catalogue.manifest["dataset_id"], "dataset_hash": catalogue.manifest["sha256"],
                "source_sha256": source, "judgment_version": "synthetic_fact_rubrics_v2", "queries": [],
                "method": "Authored synthetic rubric. Grade 3: requested role, at least one authored attribute, all optional authored title terms and specified catalogue specifications; grade 1: requested role; grade 0: other role. Labels generated before retrieval; no expected result IDs. The 65-watt preference supplies graded variation within eligible products; policy-only cases can contain many ties.",
                "metric_scope": "unbounded Commerce Ranker ordering; IDCG over independently eligible judged documents; visible Top-3 also reported"}
    for rubric in JUDGMENTS:
        labels = {p.id: relevance_grade(p,rubric) for p in catalogue.products}
        intent = FallbackIntentParser().parse(rubric["query"])
        expected = independent_eligible(catalogue.products,intent)
        eligible_labels = {pid: labels[pid] for pid in expected}
        hashes, modes = {}, {}
        for mode in ("reference", "parallel"):
            service, ranker = make_service(catalogue, SearchExecutionRequest(mode="parallel",workers=workers) if mode == "parallel" else None, runtime)
            result = service.search(None,intent)
            decisions = downstream(result,intent)
            hashes[mode] = result_digest(result,ranker,decisions)
            actual = [p.id for p in result.eligible_products]
            modes[mode] = {"ranking": ranking_metrics([r.product.id for r in ranker.all_rows],eligible_labels,k),
                           "visible": ranking_metrics([r.product.id for r in result.recommendations],eligible_labels,3),
                           "hard_filter_correct": set(actual) == set(expected),
                           "missing_eligible": sorted(set(expected)-set(actual)), "ineligible_leaks": sorted(set(actual)-set(expected)),
                           "adequacy": asdict(result.adequacy), "downstream": decisions}
        document["queries"].append({"rubric": rubric, "labels": [[pid,grade] for pid,grade in labels.items()],
            "label_sha256": sha256(canonical_json(sorted(labels.items())).encode()).hexdigest(),
            "intent_checks": {"roles": set(intent.resolved_roles) == set(rubric["roles"]),
                              "strict_max_price": str(intent.strict_max_price) == rubric["max_price"]},
            "exact_equivalence": hashes["reference"] == hashes["parallel"], "result_hashes": hashes, "modes": modes})
    document["passed"] = all(q["exact_equivalence"] and all(q["intent_checks"].values())
                               and all(m["hard_filter_correct"] for m in q["modes"].values()) for q in document["queries"])
    return document
