# Performance methodology and evidence

The first release prioritizes correctness and reproducible execution. Historical source-workspace reports use earlier source identities and workloads. They are not current release benchmark results. In particular, the prior **46% median-latency improvement** remains **unverified**.

## Small reproducible run

`benchmarks/smoke.json` specifies a 1,000-product catalogue, two workers, concurrency one, `usb c charger under 1000`, three timed trials and one warmup per mode in both `scoring` and `full` scopes. The runner compares the original serial relevance adapter against actual spawn workers. Untimed exact parity preflight precedes timing; failed observations and earlier attempts remain in the raw record.

- **Scoring:** pre-retrieval eligibility preparation, query context, lexical feature computation and global fusion. It is not just worker CPU time.
- **Full:** original search service, both commerce ranker calls, adequacy and isolated compiler/decision work. It excludes HTTP/browser rendering and catalogue generation/load setup.
- **Warm timings:** worker initialization/preflight costs are recorded separately, not quietly mixed into or subtracted from warm samples. Context-cache state can matter.
- **Throughput:** successful completed queries divided by measured trial wall duration. At concurrency one this is a narrow diagnostic, not sustained service capacity.
- **Percentiles:** the original nearest-rank estimator. Three samples are too few for a stable p95 or general performance conclusion.

The comparison is the original serial adapter versus the optimized process path, whose immutable catalogue retains a bounded context cache. The original adapter rebuilds context. A measured difference therefore includes context reuse and integration effects, not just extra processes. The workbench's serial compute mode shares pure-feature primitives and may use the immutable context cache; its request observations are not interchangeable with the experiment's original-reference baseline.

Stop the web server and other benchmark/test work before running the CLI. The benchmark capacity guard coordinates within an application process; it cannot exclude workloads in other OS processes. The release audit does not modify or use Interlock's databases, credentials, or ports and does not run the full matrix.

## Scale and interpretation

The generator produces exactly 1,000/10,000/50,000 distinct fictional products with fixed checksums. The 50K manifest has 46,357 available products. That is catalogue scale; hard constraints can leave far fewer feature candidates. Exact tests cover full-search digest parity at all three sizes using a constrained charger workload. They do not establish the latency of broad 50K full scans.

Default experiment dimensions are 3 sizes × 4 worker counts × 3 concurrency levels = 36 configurations. With scoring/full scopes that is 72 cells, before query/trial multiplicity. Existing private reports showed partial progress, not a verified completed default matrix. No completed matrix is claimed here.

Parallel execution is expected to lose on some narrow queries because spawn startup, context serialization, IPC and global coordinator work can dominate. Higher throughput and lower median latency are separate outcomes. Real relevance quality needs independent customer/catalogue judgments beyond the retained synthetic quality module.

The current release's retained measurements and their exact source/environment identity, when available, appear below in an audited follow-up commit. Until then, no numerical speedup is asserted.
