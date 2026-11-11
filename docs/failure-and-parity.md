# Failure and deterministic parity evidence

The public release tests the existing runtime and search decisions directly. It does not expose a browser chaos button. The executable tests and retained JUnit records are the evidence for the guarantees below.

| Contract | Executable evidence | What it establishes |
|---|---|---|
| Reference scalar equality | `test_hpc_serial_features.py::test_pure_serial_features_match_reference_exactly` | Same ordered feature/candidate records and hexadecimal float fields as the original relevance adapter across retained contract queries |
| Immutable snapshots | `test_snapshot_is_immutable_independent_and_preserves_prices_facts_order` | Frozen records reject mutation; later source-object mutation changes a new generation, not an existing snapshot |
| Complete search parity | `test_release.py::test_full_search_digest_and_warm_worker_reuse` | Reference and process paths agree on ordered visible/unbounded rankings, score bits, evidence, eligible products, adequacy and preview decisions for four queries at 1/2/4/8 workers |
| Scale parity | `test_hpc_catalogues.py::test_scaled_original_and_process_search_match_complete_contract` | The constrained charger workload agrees at 1K/10K/50K catalogue sizes; candidate counts remain distinct from catalogue size |
| Persistent processes | `test_full_search_digest_and_warm_worker_reuse` | Warm searches reuse owned worker PIDs and report zero repeated pool-startup cost |
| Worker death | `test_hpc_parallel.py::test_real_worker_failure_invalidates_pool_then_recovers` | An owned child exits with code 7; the broken pool is rejected and a later request rebuilds valid execution |
| Timeout cleanup | `test_timeout_terminates_only_owned_children_and_allows_recovery` | A deliberately tiny deadline invalidates owned worker resources; subsequent execution can recover |
| Generation changes | `test_generation_change_replaces_state_and_shutdown_rejects_reuse` | Modified snapshots replace the pool; stale contexts and shutdown runtime reuse are rejected |
| Corrupt results | `test_corrupt_worker_results_are_rejected` | Duplicate/stale/request/context/PID/product/nonfinite corruption cannot silently reach fusion |
| Capacity | `test_cpu_budget_and_queue_admission_are_bounded` | Unsupported worker budgets and saturated admission return explicit errors |
| FIFO and expired tickets | `test_hpc_experiments.py::test_fifo_gate_orders_waiters_and_removes_timed_out_ticket` | An expired queue ticket does not block later waiters |
| Durable experiment retry | `test_failed_query_never_completes_and_retry_keeps_failed_attempt` | Failed work does not complete a cell; a retried attempt retains its earlier error record |
| Checkpoint continuation | `test_bounded_session_resume_preserves_raw_completed_trials` | Bounded sessions resume without replacing already completed observations |
| Source/environment identity | `test_source_drift_is_persisted_failure_and_capacity_is_exclusive`, `test_resume_refuses_runtime_or_hardware_drift` | Resume refuses changed source/hardware/runtime identity rather than merging incompatible evidence |
| Bounded speaker fixes | `test_release.py` | The known misspelling returns real eligible speakers within budget, impossible budgets remain empty, and canonical role exclusions are enforced |

These are local process and controlled-workload properties. They do not prove distributed availability, production multi-tenancy, arbitrary language correctness, model parity, or invariant preservation after unreviewed code/dependency changes. The original authenticated live-commerce golden harness is excluded and is not described as having run in this repository.

See [release evidence](release-evidence.md) for actual suite counts, [architecture](architecture.md) for the boundaries, and [performance](performance.md) for why exact parity does not imply speedup.
