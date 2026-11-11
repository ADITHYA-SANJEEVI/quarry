"""Evidence-based analysis. Missing or unmatched measurements stay unavailable."""
from collections import defaultdict
from hashlib import sha256
from uuid import uuid4

from app.schemas.research import AnalysisDocument
from app.services.hpc.experiment_store import now
from app.services.hpc.snapshot import canonical_json
from app.services.hpc.tracing import overhead


def scaling_key(run, cell, *, omit_size=False, omit_concurrency=False):
    env = run["environment"]
    hardware = {k: env.get(k) for k in ("python", "packages", "platform", "logical_cpus", "physical_cpus",
                "effective_cpus", "affinity", "ram_total_mb", "cpu_quota", "cpu_quota_source")}
    dataset = next(d for d in run["datasets"] if d["count"] == cell["size"])
    identity = {"source": cell.get("source_sha256", env["source_sha256"]), "hardware": hardware,
                "algorithm": env.get("algorithm_version"), "semantic": env.get("semantic_mode"),
                "semantic_model": env.get("semantic_model"), "semantic_package": env.get("semantic_package"),
                "semantic_local_files_only": env.get("semantic_local_files_only"),
                "semantic_candidate_k":env.get("semantic_candidate_k"),
                "benchmark": env.get("benchmark_version"), "workload": run["workload_sha256"],
                "queries": run["config"]["queries"], "scope": cell["scope"],
                "research": run["config"].get("research"), "warmups": run["config"]["warmups"],
                "trials": run["config"]["trials"], "cache_policy": "persistent_snapshot_context_lru24; reference_rebuilds_context"}
    if not omit_size:
        identity["dataset"] = [dataset["dataset_id"], dataset["sha256"]]
    else:
        identity["dataset_family"] = [dataset.get("generator_version"),dataset.get("seed"),dataset.get("canonical_fact_schema")]
    if not omit_concurrency:
        identity["concurrency"] = cell["concurrency"]
    return sha256(canonical_json(identity).encode()).hexdigest()


def cost_point(run, cell, model):
    stats = cell.get("statistics", {}).get("parallel", {})
    qps = stats.get("successful_qps", 0)
    trials = [t for t in cell["trials"] if t["mode"] == "parallel"]
    resources = [t["resources"] for t in trials]
    successful = stats.get("successful", 0)
    cores = cell["workers"]+model.coordinator_cores
    hourly = model.instance_hourly if model.instance_hourly is not None else (model.core_hourly*cores if model.core_hourly is not None else None)
    peak = max((r["peak_tree_rss_mb"] for r in resources), default=None)
    cpu = sum(r["cpu_seconds"] for r in resources)/successful if successful else None
    startups = [v.get("execution", {}).get("startup_ms", 0) for v in cell.get("validation", {}).values()]
    startup = max(startups, default=0)/1000
    effective_qps = qps*model.utilization
    unit = hourly/(3600*effective_qps) if hourly is not None and effective_qps > 0 else None
    amortized_startup = hourly*startup*model.startup_sessions/(3600*model.query_volume) if hourly is not None else None
    unit = unit+amortized_startup if unit is not None else None
    p95 = stats.get("p95_ms")
    constraints = {
        "successful_measurement": bool(successful and qps > 0 and not stats.get("errors") and cell.get("statistics", {}).get("comparable")),
        "latency": p95 <= model.target_p95_ms if p95 is not None and model.target_p95_ms is not None else None,
        "throughput": effective_qps >= model.required_qps if model.required_qps is not None else None,
        "memory": peak <= model.ram_limit_mb if peak is not None and model.ram_limit_mb is not None else None,
    }
    return {"run_id": run["id"], "cell": cell["key"], "match_group": scaling_key(run, cell),
        "currency": model.currency, "allocated_cores": cores, "hourly_cost": hourly,
        "measured_successful_qps": qps, "assumed_utilization": model.utilization, "effective_qps": effective_qps,
        "qps_per_allocated_core": qps/cores if cores else None, "cpu_seconds_per_successful_query": cpu,
        "peak_tree_rss_mb": peak, "memory_kind": "sampled_peak_100ms_not_hard_upper_bound",
        "startup_seconds": startup, "startup_sessions": model.startup_sessions,
        "cost_per_successful_query": unit, "cost_per_1k": unit*1000 if unit is not None else None,
        "cost_per_1m": unit*1_000_000 if unit is not None else None,
        "startup_cost_per_query": amortized_startup, "p95_ms": p95,
        "feasibility": constraints, "feasible": all(v is not False for v in constraints.values()),
        "pricing_status": "configured" if hourly is not None else "requires_user_price",
        "formula": "hourly/(3600*measured_successful_QPS*utilization) + hourly*startup_seconds*sessions/(3600*volume)",
        "billing_assumption": "always-on allocated instance/cores; busy-query CPU time is diagnostic, not billed twice"}


def analyze(runs, request):
    groups, saturation, scaled, scaled_concurrency = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    costs, overheads, nlp = [], [], []
    for run in runs:
        for cell in run["cells"]:
            if cell["status"] != "completed" or not cell.get("statistics", {}).get("comparable"):
                continue
            stats = cell["statistics"]
            observed = [v.get("execution", {}) for k, v in cell.get("validation", {}).items() if k.startswith("parallel/")]
            # Explicit parallel measurements must actually have used the whole
            # physical pool; one-process control is never called direct serial.
            if not observed or any(v.get("actual_mode") != "parallel" or len(v.get("worker_pids", [])) != cell["workers"] for v in observed):
                continue
            point = {"run_id": run["id"], "cell": cell["key"], "size": cell["size"], "workers": cell["workers"],
                     "concurrency": cell["concurrency"], "scope": cell["scope"],
                     "sample_count": stats["parallel"]["samples"], "p50_ms": stats["parallel"]["p50_ms"],
                     "p95_ms": stats["parallel"]["p95_ms"], "qps": stats["parallel"]["successful_qps"],
                     "direct_serial_speedup": stats["latency_speedup"],
                     "throughput_speedup": stats["throughput_speedup"],
                     "latency_efficiency": stats["latency_speedup"]/cell["workers"],
                     "throughput_efficiency": stats["throughput_speedup"]/cell["workers"],
                     "small_sample_warning": stats["parallel"]["samples"] < 30}
            point["largest_preeligible_per_worker"] = cell.get("profile",{}).get("largest_preeligible_count",0)/cell["workers"]
            p, speed = cell["workers"], stats["latency_speedup"]
            point["karp_flatt_effective_serial_fraction"] = (1/speed-1/p)/(1-1/p) if p > 1 and speed else None
            point["serial_fraction_kind"] = "effective_estimate_includes_overhead; outside_[0,1]_violates_model_assumptions"
            f = request.serial_fraction
            point["projections"] = {"assumed_serial_fraction": f, "amdahl_speedup": 1/(f+(1-f)/p),
                                    "gustafson_scaled_speedup": p-f*(p-1), "kind": "model_projection_not_measurement"}
            groups[scaling_key(run, cell)].append(point)
            saturation[scaling_key(run, cell, omit_concurrency=True)+f"/{p}"].append(point)
            # Catalogue-per-worker is a defined scaled-corpus experiment, not
            # weak scaling: DF/RRF/global commerce work changes with corpus size.
            scaled[scaling_key(run, cell, omit_size=True)+f"/{cell['size']/p}"].append(point)
            scaled_concurrency[scaling_key(run,cell,omit_concurrency=True)+f"/{cell['concurrency']/p}"].append(point)
            costs.append(cost_point(run, cell, request.cost))
            for key, row in cell.get("validation", {}).items():
                if "trace" in row:
                    overheads.append(overhead(row["trace"]) | {"run_id": run["id"], "cell": cell["key"], "query_mode": key})
            for row in cell["observations"]:
                if row.get("workload"):
                    nlp.append({"run_id": run["id"], "cell": cell["key"], "mode": row["mode"], "query_index": row["query_index"],
                                "latency_ms": row["latency_ms"], "workload": row["workload"],
                                "stage_timings_ns": row.get("stage_timings_ns", [])})
    strong = []
    for identity, points in groups.items():
        controls = [v for v in points if v["workers"] == 1]
        for point in points:
            control = controls[0] if len(controls) == 1 else None
            point["subprocess_p1_speedup"] = control["p50_ms"]/point["p50_ms"] if control and point["p50_ms"] else None
            point["subprocess_p1_efficiency"] = point["subprocess_p1_speedup"]/point["workers"] if point["subprocess_p1_speedup"] is not None else None
        strong.append({"match_group": identity, "points": points,
                       "status": "matched_curve" if len({p['workers'] for p in points}) > 1 else "requires_more_worker_counts"})
    saturation_result = []
    for identity, points in saturation.items():
        points = sorted(points, key=lambda p: p["concurrency"])
        saturation_result.append({"match_group": identity, "points": points,
            "throughput_saturated_or_regressed": any(b["concurrency"] > a["concurrency"] and b["qps"] <= a["qps"] for a,b in zip(points,points[1:])),
            "status": "measured" if len({p['concurrency'] for p in points}) > 1 else "requires_more_concurrency_levels"})
    for point in costs:
        peers = [other for other in costs if other["match_group"] == point["match_group"] and other["feasible"]
                 and other["cost_per_successful_query"] is not None and other["p95_ms"] is not None]
        point["pareto"] = (point["feasible"] and point["cost_per_successful_query"] is not None and
            not any(o["cost_per_successful_query"] <= point["cost_per_successful_query"] and o["p95_ms"] <= point["p95_ms"]
                    and (o["cost_per_successful_query"] < point["cost_per_successful_query"] or o["p95_ms"] < point["p95_ms"]) for o in peers))
    break_even = []
    # Total volume cost = per-search steady cost*V + one-off startup cost.
    for i, a in enumerate(costs):
        for b in costs[i+1:]:
            if a["match_group"] != b["match_group"] or a["cost_per_successful_query"] is None or b["cost_per_successful_query"] is None:
                continue
            fixed_a = a["startup_cost_per_query"]*request.cost.query_volume
            fixed_b = b["startup_cost_per_query"]*request.cost.query_volume
            delta = (a["cost_per_successful_query"]-a["startup_cost_per_query"])-(b["cost_per_successful_query"]-b["startup_cost_per_query"])
            volume = (fixed_b-fixed_a)/delta if delta else None
            break_even.append({"left": [a["run_id"],a["cell"]], "right": [b["run_id"],b["cell"]],
                               "query_volume": volume if volume is not None and volume > 0 else None,
                               "kind": "constant_measured_QPS_and_fixed_startup_projection"})
    return AnalysisDocument(id=str(uuid4()), created_at=now(), run_ids=request.run_ids, model=request,
        scaling={"strong": strong, "saturation": saturation_result,
                 "scaled_workload": [{"match_group": k, "points": v,
                     "definition": "fixed catalogue documents per worker; same query list and concurrency; corpus statistics change",
                     "status": "measured_scaled_corpus_not_weak_scaling" if len({p['size'] for p in v}) > 1 else "requires_matching_catalogue_per_worker_runs"} for k,v in scaled.items()],
                 "scaled_concurrency": [{"match_group":k,"points":v,
                     "definition":"fixed catalogue and query mix; offered concurrent callers per physical worker held constant; FIFO coordinator remains shared",
                     "status":"measured_scaled_offered_load_not_weak_scaling" if len({p['workers'] for p in v}) > 1 else "requires_matching_callers_per_worker_runs"} for k,v in scaled_concurrency.items()],
                 "matching_policy": "exact source epoch, dataset/hash, query order, semantic mode, scope, parser inclusion, hardware, packages, warmup/cache policy, trials"},
        overhead=overheads, nlp=nlp,
        cost={"points": costs, "break_even": break_even,
              "recommended": [[p["run_id"],p["cell"]] for p in costs if p["pareto"]],
              "recommendation_basis": "feasible measured latency/cost Pareto points within match groups; missing prices produce no recommendation"})
