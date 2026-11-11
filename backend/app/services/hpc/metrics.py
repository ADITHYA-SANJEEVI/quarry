from math import ceil
from threading import Event, Thread
from time import perf_counter
import os
import platform
import sys
from importlib.metadata import version

import psutil


def percentile(values, p):
    """Nearest-rank percentile; defined for one sample and repeated values."""
    values = sorted(values)
    return values[max(0, ceil(len(values) * p) - 1)] if values else None


def aggregate(observations, trials, workers):
    result = {}
    for mode in ("reference", "parallel"):
        samples = [s for s in observations if s["mode"] == mode]
        successful = [s for s in samples if s.get("error") is None]
        times = [s["latency_ms"] for s in successful]
        seconds = sum(t["wall_seconds"] for t in trials if t["mode"] == mode)
        result[mode] = {"samples": len(samples), "successful": len(successful), "errors": len(samples)-len(successful),
                        "p50_ms": percentile(times, .5), "p95_ms": percentile(times, .95),
                        "successful_qps": len(successful)/seconds if seconds else 0,
                        "parity_passed": sum(s.get("parity") is True for s in successful),
                        "parity_failed": sum(s.get("parity") is False for s in successful)}
    a, b = result["reference"], result["parallel"]
    valid = (a["successful"] == a["samples"] and b["successful"] == b["samples"] and a["successful"] == b["successful"]
             and b["successful"] > 0 and not a["parity_failed"] and not b["parity_failed"])
    result["comparable"] = valid
    result["latency_speedup"] = a["p50_ms"]/b["p50_ms"] if valid and b["p50_ms"] else None
    result["throughput_speedup"] = b["successful_qps"]/a["successful_qps"] if valid and a["successful_qps"] else None
    result["parallel_efficiency"] = result["throughput_speedup"]/workers if valid else None
    return result


def cpu_allocation():
    process = psutil.Process()
    affinity = process.cpu_affinity() if hasattr(process, "cpu_affinity") else list(range(os.cpu_count() or 1))
    # cgroup quota matters on Kaggle/containers even when host CPU count is large.
    quota_cpus = len(affinity)
    quota_value, quota_source = None, None
    try:
        from pathlib import Path
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            quota_value = int(quota)/int(period)
            quota_source = "cgroup_v2"
    except (OSError, ValueError):
        try:
            from pathlib import Path
            quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
            period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
            if quota > 0:
                quota_value, quota_source = quota/period, "cgroup_v1"
        except (OSError, ValueError):
            pass
    if quota_value is not None:
        quota_cpus = min(quota_cpus, max(1, int(quota_value)))
    return {"logical_cpus": os.cpu_count(),
            "affinity": affinity, "effective_cpus": quota_cpus, "physical_cpus": psutil.cpu_count(logical=False),
            "cpu_quota": quota_value, "cpu_quota_source": quota_source}


def environment():
    return cpu_allocation() | {"python": sys.version.split()[0], "platform": platform.platform(),
            "ram_total_mb": psutil.virtual_memory().total/2**20, "ram_available_mb": psutil.virtual_memory().available/2**20,
            "packages": {name: version(name) for name in ("psutil", "SQLAlchemy", "pydantic", "rapidfuzz")}}


class ResourceSampler:
    def __init__(self, limit_mb, interval=.1):
        self.limit_mb, self.interval = limit_mb, interval
        self.samples, self.exceeded = [], Event()
        self.stop_event = Event()
        self.process = psutil.Process()
        self.cpu_seen = {}
        self.origin = perf_counter()
        self.cpu_start = self._cpu()
        self.thread = Thread(target=self._loop, daemon=True)

    def _cpu(self):
        processes = [self.process] + self.process.children(recursive=True)
        for process in processes:
            try:
                t = process.cpu_times()
                self.cpu_seen[(process.pid,process.create_time())] = t.user + t.system
            except psutil.Error:
                pass
        return sum(self.cpu_seen.values())

    def sample(self):
        children, child_mb = [], 0
        for process in self.process.children(recursive=True):
            try:
                memory = process.memory_info().rss/2**20
                cpu = process.cpu_times()
                children.append({"pid": process.pid, "rss_mb": memory,
                                 "cpu_seconds_cumulative": cpu.user+cpu.system,
                                 "process_created_at": process.create_time()}); child_mb += memory
            except psutil.Error:
                pass
        parent_mb = self.process.memory_info().rss/2**20
        point = {"elapsed_seconds": perf_counter()-self.origin, "parent_rss_mb": parent_mb,
                 "children_rss_mb": child_mb, "children": children, "system_available_mb": psutil.virtual_memory().available/2**20}
        cpu = self.process.cpu_times()
        point["parent_cpu_seconds_cumulative"] = cpu.user+cpu.system
        if parent_mb + child_mb > self.limit_mb or point["system_available_mb"] < 256:
            self.exceeded.set()
        self.samples.append(point)

    def _loop(self):
        while not self.stop_event.wait(self.interval):
            self.sample()

    def __enter__(self):
        self.sample(); self.thread.start(); return self

    def __exit__(self, *args):
        self.stop_event.set(); self.thread.join(); self.sample()
        duration = perf_counter()-self.origin
        cpu = max(0, self._cpu()-self.cpu_start)
        self.summary = {"cpu_seconds": cpu, "wall_seconds": duration,
                        "cpu_percent_one_core": cpu/duration*100,
                        "peak_parent_rss_mb": max(p["parent_rss_mb"] for p in self.samples),
                        "peak_children_rss_mb": max(p["children_rss_mb"] for p in self.samples),
                        "peak_tree_rss_mb": max(p["parent_rss_mb"]+p["children_rss_mb"] for p in self.samples),
                        "memory_limit_exceeded": self.exceeded.is_set()}
