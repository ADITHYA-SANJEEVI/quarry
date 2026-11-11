"""python -m app.services.hpc.cli: same offline datasets/engine as the API."""
import argparse
import json
import multiprocessing
from pathlib import Path

from app.schemas.experiments import ExperimentConfig
from app.services.hpc.catalogues import CatalogueRegistry, DATA_ROOT, SIZES, generate_catalogue
from app.services.hpc.experiment_store import ExperimentStore
from app.services.hpc.experiments import ExperimentRunner, export_csv, owner_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--runs-root", type=Path, default=Path("artifacts/experiments"))
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--sizes", nargs="+", type=int, choices=SIZES, default=list(SIZES))
    run = commands.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    resume = commands.add_parser("resume"); resume.add_argument("run_id")
    export = commands.add_parser("export"); export.add_argument("run_id"); export.add_argument("--output", required=True, type=Path)
    commands.add_parser("history")
    args = parser.parse_args()
    if args.command == "generate":
        for size in args.sizes:
            manifest, profile = generate_catalogue(args.data_root, size)
            print(json.dumps({"manifest": manifest, "profile": profile}))
        return
    store = ExperimentStore(args.runs_root)
    runner = ExperimentRunner(store, CatalogueRegistry(args.data_root))
    if args.command == "run":
        config = ExperimentConfig(**json.loads(args.config.read_text(encoding="utf-8-sig")))
        run = runner.create(config)
        print(json.dumps({"id": run["id"], "status": run["status"]}), flush=True)
        run = runner.execute(run["id"])
    elif args.command == "resume":
        run = runner.execute(args.run_id)
    elif args.command == "export":
        run = store.get(args.run_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        content = export_csv(run) if args.output.suffix.lower() == ".csv" else json.dumps(run, indent=2)
        args.output.write_text(content + "\n", encoding="utf-8")
        print(str(args.output)); return
    else:
        print(json.dumps([{k:r[k] for k in ("id","status","progress")} for r in store.history()], indent=2)); return
    print(json.dumps({"id": run["id"], "status": run["status"], "progress": run["progress"],
                      "errors": run["errors"], "cells": [{"key": c["key"], "status": c["status"],
                      "statistics": c.get("statistics"), "errors": c["errors"]} for c in run["cells"]]}, indent=2))
    if run["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
