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

The configured first warmup is the untimed parity preflight itself; `warmups=1` does not add another warmup after it. Timed observations carry `parity_scope=exact_untimed_preflight_same_immutable_inputs`: their parity flag refers to that validated input/configuration, not an extra digest comparison inside each timed interval.

Stop the web server and other benchmark/test work before running the CLI. The benchmark capacity guard coordinates within an application process; it cannot exclude workloads in other OS processes. The release audit does not modify or use Interlock's databases, credentials, or ports and does not run the full matrix.

## Scale and interpretation

The generator produces exactly 1,000/10,000/50,000 distinct fictional products with fixed checksums. The 50K manifest has 46,357 available products. That is catalogue scale; hard constraints can leave far fewer feature candidates. Exact tests cover full-search digest parity at all three sizes using a constrained charger workload. They do not establish the latency of broad 50K full scans.

Default experiment dimensions are 3 sizes × 4 worker counts × 3 concurrency levels = 36 configurations. With scoring/full scopes that is 72 cells, before query/trial multiplicity. Existing private reports showed partial progress, not a verified completed default matrix. No completed matrix is claimed here.

Parallel execution is expected to lose on some narrow queries because spawn startup, context serialization, IPC and global coordinator work can dominate. Higher throughput and lower median latency are separate outcomes. Real relevance quality needs independent customer/catalogue judgments beyond the retained synthetic quality module.

## Measured local diagnostic — 12 November 2026

Run `597f0344-4c03-4f41-bd46-e8f119c8feec` completed **2/2 cells with zero errors**. Each mode/scope has **three timed samples**. Both untimed reference/process digest comparisons matched exactly. The 1K catalogue left **44 pre-eligible feature candidates** for this query; full-search final eligibility contained **16 products**. These are not 1,000-document feature timings.

| Scope | Original reference p50 | Two-process path p50 | Reference successful QPS | Process successful QPS |
|---|---:|---:|---:|---:|
| Scoring | 101.18 ms | 55.82 ms | 9.04 | 16.63 |
| Full search | 110.92 ms | 67.09 ms | 8.20 | 14.63 |

**Interpretation:** this is a small warm-path diagnostic including context-cache differences, not an isolated multiprocessing speedup or a production latency claim. Cold pool startup alone took **4,686.93 ms** in scoring preflight and **4,843.46 ms** in full-search preflight; these costs are outside the warm p50 values above. Short-lived processes can therefore be much slower despite the lower warm samples. The measured timings exclude HTTP and browser rendering. Three samples cannot establish a reliable tail distribution or broad scaling result.

The host reported **6 physical / 12 logical and allocated CPUs**, approximately **15.3 GiB RAM**, **Windows 11**, and **Python 3.12.10**. Only two workers were used. The application server and this release's test runs were stopped; no Interlock pytest process was detected before starting. Unrelated host activity was not controlled, and trials were not randomized or repeated across independent sessions.

Measured source commit: `4a468bd051bb620de5d9c2f9c854b55e9165a993`.
Normalized application Python-source SHA-256: `51cd135d9167c109ade67d2358f738e56f3f192d5714b8af34723fdc163b545c`.

Reinspect or recompute the summary from [raw JSON](evidence/smoke-run.json) and [timed observation CSV](evidence/smoke-run.csv). The JSON retains source/environment identity, dataset hashes, full configuration, parity preflight digests, startup costs, individual observations, trial wall times, and sampled resources. No historical output was relabelled as this run. The historical 46% claim remains unverified.
