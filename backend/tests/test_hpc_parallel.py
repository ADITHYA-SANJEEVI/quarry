from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.schemas.hpc import SearchExecutionRequest
from app.services.hpc.context import build_context
from app.services.hpc.contracts import ComputeError, FeatureBatch, FeatureTask
from app.services.hpc.engine import fuse_features
from app.services.hpc.features import score_partition
from app.services.hpc.integration import ExecutionSearchService
from app.services.hpc.runtime import ProcessRuntime, balanced_partitions, validate_batches
from app.services.hpc.snapshot import capture_catalogue
from app.services.intent_parser import FallbackIntentParser
from app.services.commerce_ranker import CommerceRanker
from tests.fixtures.evaluation_catalogue import hydrated_evaluation_catalogue


def computation(query="cleanser under 700"):
    products = hydrated_evaluation_catalogue()
    snapshot = capture_catalogue(products)
    context = build_context(snapshot, products, FallbackIntentParser().parse(query))
    return products, snapshot, context




def test_concurrent_contexts_cannot_corrupt_each_other():
    products, snapshot, first = computation()
    second = build_context(snapshot, products, FallbackIntentParser().parse("charger"))
    with ProcessRuntime(max_workers=2) as runtime:
        def run(context):
            rows, metrics = runtime.score(snapshot, context, 2)
            assert metrics["worker_pids"]
            return rows
        with ThreadPoolExecutor(max_workers=4) as callers:
            contexts = [first, second, first, second]
            actual = list(callers.map(run, contexts))
        assert actual == [score_partition(snapshot, c, c.ordinals) for c in contexts]


def test_large_snapshot_startup_starts_every_requested_worker():
    from app.db.seed_data import DEMO_PRODUCTS
    from app.models.product import Product
    if (os.cpu_count() or 1) < 8:
        pytest.skip("eight worker initialization requires eight allocated CPUs")
    products = []
    for i in range(400):
        data = dict(DEMO_PRODUCTS[i % len(DEMO_PRODUCTS)])
        data["name"] += f" startup{i:05d}"
        products.append(Product(id=i + 1, **data))
    snapshot = capture_catalogue(products)
    context = build_context(snapshot, products, FallbackIntentParser().parse("cleanser"))
    with ProcessRuntime(max_workers=8) as runtime:
        first, cold = runtime.score(snapshot, context, 8)
        second, warm = runtime.score(snapshot, context, 8)
        assert len(cold["worker_pids"]) == 8
        assert warm["worker_pids"] == cold["worker_pids"]
        assert warm["startup_ms"] == 0
        assert first == second == score_partition(snapshot, context, context.ordinals)


def test_generation_change_replaces_state_and_shutdown_rejects_reuse():
    products, snapshot, context = computation()
    runtime = ProcessRuntime(max_workers=1)
    try:
        _, first = runtime.score(snapshot, context, 1)
        products[0].stock = 0
        updated = capture_catalogue(products)
        query = build_context(updated, products, FallbackIntentParser().parse("charger"))
        rows, second = runtime.score(updated, query, 1)
        assert updated.generation != snapshot.generation
        assert first["worker_pids"] != second["worker_pids"]
        assert rows == score_partition(updated, query, query.ordinals)
        with pytest.raises(ComputeError, match="generation_mismatch"):
            runtime.score(updated, context, 1)
    finally:
        runtime.shutdown()
    with pytest.raises(ComputeError, match="runtime_closed"):
        runtime.score(snapshot, context, 1)


def kill_worker():
    os._exit(7)


def test_real_worker_failure_invalidates_pool_then_recovers():
    _, snapshot, context = computation()
    with ProcessRuntime(max_workers=1) as runtime:
        runtime.score(snapshot, context, 1)
        with pytest.raises(Exception):
            runtime._pool.submit(kill_worker).result(timeout=10)
        with pytest.raises(ComputeError, match="worker_failure"):
            runtime.score(snapshot, context, 1)
        rows, _ = runtime.score(snapshot, context, 1)
        assert rows == score_partition(snapshot, context, context.ordinals)


def test_timeout_terminates_only_owned_children_and_allows_recovery():
    import multiprocessing
    _, snapshot, context = computation()
    with ProcessRuntime(max_workers=1) as runtime:
        _, initial = runtime.score(snapshot, context, 1)
        runtime.timeout_seconds = .000001
        with pytest.raises(ComputeError, match="worker_timeout"):
            runtime.score(snapshot, context, 1)
        assert not set(initial["worker_pids"]) & {p.pid for p in multiprocessing.active_children()}
        runtime.timeout_seconds = 30
        rows, _ = runtime.score(snapshot, context, 1)
        assert rows == score_partition(snapshot, context, context.ordinals)


def test_cpu_budget_and_queue_admission_are_bounded():
    _, snapshot, context = computation()
    with ProcessRuntime(max_workers=1, queue_timeout_seconds=.01, max_pending=1) as runtime:
        with pytest.raises(ComputeError, match="cpu_budget"):
            runtime.score(snapshot, context, 2)
        runtime._pending.acquire()
        try:
            with pytest.raises(ComputeError, match="admission_timeout"):
                runtime.score(snapshot, context, 1)
        finally:
            runtime._pending.release()
        runtime._lock.acquire()
        try:
            with pytest.raises(ComputeError, match="queue_timeout"):
                runtime.score(snapshot, context, 1)
        finally:
            runtime._lock.release()
        assert runtime._pool is None


@pytest.mark.parametrize("count", [0, 1, 2, 7, 31])
@pytest.mark.parametrize("workers", [1, 2, 4, 8])
def test_balanced_partitions_are_disjoint_and_complete(count, workers):
    parts = balanced_partitions(tuple(range(count)), workers)
    assert tuple(i for part in parts for i in part) == tuple(range(count))
    assert max(map(len, parts)) - min(map(len, parts)) <= 1


@pytest.mark.parametrize("corruption", ["duplicate", "missing", "stale", "request", "context", "pid", "product", "nan"])
def test_corrupt_worker_results_are_rejected(corruption):
    _, snapshot, context = computation()
    task = FeatureTask("request-a", 0, snapshot.generation, context, context.ordinals, 0)
    rows = score_partition(snapshot, context, context.ordinals)
    batch = FeatureBatch(task.request_id, 0, snapshot.generation, context.context_id, rows, 123, 0, 0)
    batches = [batch]
    if corruption == "duplicate": batches *= 2
    elif corruption == "missing": batches = []
    elif corruption == "stale": batches = [replace(batch, generation="old")]
    elif corruption == "request": batches = [replace(batch, request_id="other")]
    elif corruption == "context": batches = [replace(batch, context_id="other")]
    elif corruption == "pid": batches = [replace(batch, pid=999)]
    elif corruption == "product": batches = [replace(batch, rows=(replace(rows[0], product_id=-1), *rows[1:]))]
    elif corruption == "nan": batches = [replace(batch, rows=(replace(rows[0], lexical=float("nan")), *rows[1:]))]
    with pytest.raises(ComputeError):
        validate_batches(snapshot, context, [task], batches, [123])


def test_empty_and_tied_rows_and_fallback_are_exact():
    from app.services.hpc.integration import ComputeRelevanceAdapter
    from app.services.relevance import DeterministicRelevanceAdapter
    from app.models.product import Product
    from decimal import Decimal
    intent = FallbackIntentParser().parse("cleanser")
    adapter = ComputeRelevanceAdapter(SearchExecutionRequest(mode="parallel"))
    assert adapter.retrieve([], intent) == []
    assert adapter.metadata.worker_pids == []
    products = [Product(id=i, name="Tie", description="cleanser", category="skincare", price=Decimal("100.00"), tags=["cleanser"], stock=1) for i in [3, 1, 2]]
    expected = DeterministicRelevanceAdapter().retrieve(products, intent)
    adapter = ComputeRelevanceAdapter(SearchExecutionRequest(mode="parallel", allow_fallback=True))
    assert adapter.retrieve(products, intent) == expected
    assert adapter.metadata.actual_mode == "serial" and adapter.metadata.fallback_reason == "runtime_unavailable"
    with pytest.raises(ComputeError, match="runtime_unavailable"):
        ComputeRelevanceAdapter(SearchExecutionRequest(mode="parallel")).retrieve(products, intent)
