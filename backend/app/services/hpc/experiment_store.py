"""Local durable runs: atomic replace, explicit recovery, no database writes."""
from datetime import datetime, UTC
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from app.services.hpc.snapshot import canonical_json
from app.services.hpc.storage_io import atomic_replace


def now():
    return datetime.now(UTC).isoformat()


class ExperimentStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def _path(self, run_id):
        # Never accept a path or a partially matching UUID.
        if str(UUID(run_id)) != run_id:
            raise ValueError("invalid_run_id")
        return self.root / f"{run_id}.json"

    def save(self, run):
        with self.lock:
            path = self._path(run["id"])
            temporary = path.with_suffix(".json.tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(run, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
            atomic_replace(temporary, path)

    def get(self, run_id):
        with self.lock:
            return json.loads(self._path(run_id).read_text(encoding="utf-8"))

    def history(self):
        with self.lock:
            return sorted((json.loads(p.read_text(encoding="utf-8")) for p in self.root.glob("[0-9a-f]*.json")),
                          key=lambda r: r["created_at"], reverse=True)

    def create(self, config, metadata, manifests):
        config = config.model_dump(mode="json")
        workload = sha256(canonical_json(config["queries"]).encode()).hexdigest()
        cells = [{"key": f"{n}/{p}/{c}/{scope}", "size": n, "workers": p, "concurrency": c, "scope": scope,
                  "status": "queued", "observations": [], "trials": [], "errors": []}
                 for n in config["sizes"] for p in config["workers"] for c in config["concurrency"] for scope in config["scopes"]]
        run = {"schema_version": "hpc_experiment_v1", "id": str(uuid4()), "status": "queued", "config": config,
               "created_at": now(), "updated_at": now(), "started_at": None, "finished_at": None,
               "workload_sha256": workload, "datasets": manifests, "environment": metadata,
               "cells": cells, "errors": [], "cancel_requested": False, "sessions": [],
               "progress": {"completed_cells": 0, "total_cells": len(cells)}}
        self.save(run)
        return run

    def recover(self):
        # Only invoke under one server/CLI owner, never from a read endpoint.
        for run in self.history():
            if run["status"] == "running":
                run["status"] = "failed"
                run["errors"].append({"category": "interrupted_process", "at": now()})
                run["updated_at"] = now()
                for cell in run["cells"]:
                    if cell["status"] == "running":
                        cell["status"] = "queued"
                self.save(run)

    def cancel(self, run_id):
        with self.lock:
            run = self.get(run_id)
            if run["status"] in ("completed", "cancelled", "failed"):
                return run
            run["cancel_requested"] = True
            if run["status"] == "queued":
                run["status"] = "cancelled"; run["finished_at"] = now()
            run["updated_at"] = now(); self.save(run)
            return run
