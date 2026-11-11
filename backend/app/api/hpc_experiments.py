"""Versioned isolated catalogue and authorized experiment HTTP contracts."""
from decimal import Decimal
from secrets import compare_digest
from threading import BoundedSemaphore, Lock
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response

from app.core.config import get_settings
from app.schemas.experiments import DemoSearchRequest, ExperimentConfig
from app.services.hpc.catalogues import registry
from app.services.hpc.contracts import ComputeError, VERSION
from app.services.hpc.demo_search import search_catalogue
from app.services.hpc.experiment_store import ExperimentStore
from app.services.hpc.experiments import ExperimentRunner, ExperimentManager, compatible, export_csv
from app.services.hpc.metrics import environment
from app.services.hpc.runtime import get_runtime
from app.services.intent_parser import FallbackIntentParser

router = APIRouter(prefix="/api/v1/hpc", tags=["HPC v1"])
_manager = None
_manager_lock = Lock()
_search_slots = BoundedSemaphore(2)


def authorize(authorization: str | None = Header(default=None)):
    secret = get_settings().hpc_admin_token
    if not secret:
        raise HTTPException(503, "HPC experiment administration is not configured.")
    supplied = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
    if not compare_digest(supplied.encode(), secret.get_secret_value().encode()):
        raise HTTPException(403, "HPC experiment authorization required.")


def manager():
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = ExperimentManager(ExperimentRunner(ExperimentStore(get_settings().hpc_experiment_root), registry))
        return _manager


def shutdown_experiments():
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.shutdown(); _manager = None


def catalogue(dataset_id):
    try:
        return registry.get(dataset_id)
    except (KeyError, FileNotFoundError):
        raise HTTPException(404, "Unknown or ungenerated dataset.")
    except ValueError:
        raise HTTPException(503, "Dataset integrity verification failed.")


def get_run(run_id):
    try:
        service = manager()
        run = service.runner.store.get(run_id)
        # Response-only scheduling state: a queued checkpoint may already have a
        # live owner. Never persist this transient flag or infer worker activity.
        with service.lock:
            future = service.pending.get(run_id)
            run["manager_scheduled"] = future is not None and not future.done()
        return run
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Experiment not found.")


@router.get("/capabilities")
def capabilities():
    hardware = environment()
    return {"schema_version": "hpc_capabilities_v1", "algorithm_version": VERSION,
            "hpc_enabled": get_settings().hpc_enabled,
            "modes": ["serial", "parallel", "auto"], "worker_counts": [p for p in (1,2,4,8) if p <= min(hardware["effective_cpus"], get_settings().hpc_max_workers)],
            "query_concurrency": [1,8,32], "catalogue_sizes": [1000,10000,50000], "semantic_mode": "disabled_deterministic_lexical",
            "experiment_auth": "server_only_bearer_token", "experiment_configured": bool(get_settings().hpc_admin_token),
            "scheduling": "bounded_fifo_feature_batches_one_persistent_pool_per_run_cell", "demo_checkout": False,
            "dataset_selection": "explicit_per_request", "scopes": ["scoring", "full"]}


@router.get("/datasets")
def datasets():
    return {"schema_version": "hpc_datasets_v1", "items": registry.manifests()}


@router.get("/catalogues/{dataset_id}/products")
def products(dataset_id: str, page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100),
             category: str | None = None, min_price: Decimal | None = Query(None, ge=0),
             max_price: Decimal | None = Query(None, ge=0), availability: Literal["available","unavailable","all"] = "available",
             sort: Literal["id","price_asc","price_desc","name"] = "id"):
    provider = catalogue(dataset_id)
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(422, "min_price must not exceed max_price.")
    rows = [p for p in provider.products if (category is None or p.category == category)
            and (min_price is None or p.price >= min_price) and (max_price is None or p.price <= max_price)
            and (availability == "all" or (p.catalogue_active and p.stock > 0) == (availability == "available"))]
    keys = {"id": lambda p: p.id, "price_asc": lambda p: (p.price,p.id),
            "price_desc": lambda p: (-p.price,p.id), "name": lambda p: (p.name,p.id)}
    rows.sort(key=keys[sort]); total = len(rows); offset = (page-1)*page_size
    return {"schema_version": "hpc_products_v1", "dataset": provider.manifest,
            "items": [provider.wire(p) for p in rows[offset:offset+page_size]], "page": page, "page_size": page_size,
            "total": total, "pages": (total+page_size-1)//page_size}


@router.get("/catalogues/{dataset_id}/products/{product_id}")
def product_detail(dataset_id: str, product_id: str):
    provider = catalogue(dataset_id)
    prefix = f"demo:{dataset_id}:"
    if not product_id.startswith(prefix) or not product_id[len(prefix):].isdigit():
        raise HTTPException(404, "Product identity does not belong to this dataset.")
    product = provider.by_id.get(int(product_id[len(prefix):]))
    if product is None:
        raise HTTPException(404, "Product not found.")
    return provider.wire(product)


@router.post("/catalogues/{dataset_id}/search")
def search(dataset_id: str, request: DemoSearchRequest):
    if not get_settings().hpc_enabled:
        raise HTTPException(503, "HPC compute is disabled.")
    if not _search_slots.acquire(blocking=False):
        raise HTTPException(429, "Demo search capacity is busy.")
    try:
        provider = catalogue(dataset_id)
        runtime = get_runtime() if request.execution.mode != "serial" else None
        if request.execution.workers and runtime:
            runtime.validate_workers(request.execution.workers)
        return search_catalogue(provider, FallbackIntentParser().parse(request.query), request.execution, runtime)
    except ComputeError as error:
        raise HTTPException(503, str(error))
    finally:
        _search_slots.release()


@router.post("/experiments", dependencies=[Depends(authorize)], status_code=202)
def create_experiment(config: ExperimentConfig):
    service = manager()
    if max(config.workers) > min(environment()["effective_cpus"], get_settings().hpc_max_workers):
        raise HTTPException(422, "Requested workers exceed the configured CPU budget.")
    try:
        run = service.runner.create(config)
        service.submit(run["id"])
        return {"id": run["id"], "status": run["status"], "progress": run["progress"]}
    except ValueError as error:
        raise HTTPException(409, str(error))


@router.get("/experiments", dependencies=[Depends(authorize)])
def history(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    runs = manager().runner.store.history()
    return {"total": len(runs), "items": [{k: r[k] for k in ("id","status","created_at","updated_at","progress","config","workload_sha256")} for r in runs[offset:offset+limit]]}


@router.get("/experiments/compare", dependencies=[Depends(authorize)])
def compare(left: str, right: str):
    a, b = get_run(left), get_run(right)
    if not compatible(a,b):
        raise HTTPException(409, "Runs have incompatible datasets, workloads or source semantics.")
    return {"compatible": True, "left": a, "right": b}


@router.get("/experiments/{run_id}", dependencies=[Depends(authorize)])
def status(run_id: str):
    run = get_run(run_id)
    return {k: run[k] for k in ("id","status","progress","created_at","updated_at","started_at","finished_at","errors","config","manager_scheduled")}


@router.get("/experiments/{run_id}/results", dependencies=[Depends(authorize)])
def results(run_id: str):
    return get_run(run_id)


@router.post("/experiments/{run_id}/resume", dependencies=[Depends(authorize)], status_code=202)
def resume(run_id: str):
    run = get_run(run_id)
    if run["status"] not in ("queued","failed") or run["cancel_requested"]:
        raise HTTPException(409, "Only queued or interrupted/failed runs can resume.")
    if max(run["config"]["workers"]) > min(environment()["effective_cpus"],get_settings().hpc_max_workers):
        raise HTTPException(422, "Requested workers exceed the configured CPU budget.")
    try:
        manager().submit(run_id)
    except ValueError as error:
        raise HTTPException(409, str(error))
    return {"id": run_id, "status": "queued"}


@router.post("/experiments/{run_id}/cancel", dependencies=[Depends(authorize)])
def cancel(run_id: str):
    get_run(run_id)
    return manager().runner.store.cancel(run_id)


@router.get("/experiments/{run_id}/export", dependencies=[Depends(authorize)])
def export(run_id: str, format: Literal["json","csv"] = "json"):
    run = get_run(run_id)
    if format == "csv":
        return Response(export_csv(run), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{run_id}.csv"'})
    return run
