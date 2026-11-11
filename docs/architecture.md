# Architecture and execution contract

Quarry extracts an existing deterministic commerce engine into an offline application. `app.main` mounts only the HPC catalogue/experiment API and static workbench. It creates no database engine, connects to no payment gateway, and registers no authentication, merchant, order, or payment routes.

## Query processing

`FallbackIntentParser` normalizes a reviewed vocabulary into typed role clauses, requirements, price budgets and provenance. It retains raw text and ambiguity rather than asking a model to choose inventory. `ConstraintEngine.pre_retrieval` applies cheap availability, budget, exclusion and compatibility rules. Canonical roles are enforced at the final gate to preserve the established candidate-relative scoring contract.

`CatalogueRegistry` accepts only three explicit dataset IDs, verifies JSONL hashes/counts/eligibility, and materializes frozen product views. Snapshot records use frozen dataclasses and tuples across the process boundary. A content generation hash binds the complete catalogue state. The registry retains one loaded generation; an immutable generation has a bounded 24-entry query-context LRU. Repeated context hits are observable and may affect warm timings.

## Retrieval and global decisions

The original relevance adapter remains the serial reference. `build_context` computes the corpus/query statistics used by every partition. `score_partition` reuses its pure BM25/fuzzy/trigram primitives and arithmetic. `fuse_features` validates complete membership before globally sorting and fusing lane rankings. No shard performs local top-k pruning.

`ExecutionSearchService` wraps the reference search service with the compute adapter. Both routes then use the same final eligibility, commerce ranker, adequacy policy, and compiler. The ranker has visible and unbounded calls; both remain coordinator work. The compiler's preview subclass replaces persistence with a content-derived preview identifier. No synthetic product becomes a live proposition or purchase.

Fusion uses three global lanes (lexical, fuzzy and character): `rrf = sum(1 / (60 + rank))`; the candidate feature score is `min(1, 22 * rrf + 0.25 * phrase)`. Lane ties use product ID, and fused candidates sort by descending fused score, then price and name. The downstream commerce ranker has its own evidence/preference decomposition; the workbench's explanations come from that existing ranker rather than an external language model.

## Scheduling, backpressure, and IPC

`ProcessRuntime` owns one persistent `ProcessPoolExecutor` using `spawn`. It initializes every requested child with an immutable snapshot and waits for ready signals. The pool key is `(generation, workers)`. A bounded semaphore limits admitted work (default eight); `FairGate` admits one batch at a time in FIFO order with timeout removal. Two application-level search slots bound concurrent synthetic requests.

Partitions are balanced disjoint ordinal tuples. Tasks include request ID, partition ID, context ID and generation. Each result must come from an owned PID and match exact partition/document membership, version and context. Valid rows return in canonical ordinal order before fusion. Validation rejects duplicate partitions, missing rows, unknown products, stale generations and NaN/Infinity.

The snapshot is replicated in worker address spaces. Each task serializes query context; feature rows return through executor IPC. Increasing worker count can increase startup, memory, serialization and coordinator overhead. Execution metadata reports startup, admission queue, dispatch queues, child execution and global fusion. Research traces also record request-local spans, worker CPU/RSS and serialization probes. Their residual intervals include scheduling/transport; they are not direct kernel IPC timings. Summed overlapping spans are not interchangeable with end-to-end latency.

## Failures and recovery

A broken child, invalid batch or worker timeout invalidates the owned pool. Forced teardown targets only that executor's children. A later admitted request rebuilds state. When fallback is allowed, the same immutable features can be recomputed serially and the response reports the reason and actual mode. Strict parallel mode returns the error. Admission and queue timeout do not silently widen capacity.

Experiments reserve the in-process compute runtime, write atomic local checkpoints, retain failed attempts, and compare dataset/workload/source/environment identities before resume. A single-process owner lock guards each run-store root. A process crash may leave a stale owner record; liveness/creation identity permits explicit recovery. These are local storage/process guarantees; separate OS processes still require operator resource isolation.

Tests deliberately terminate an owned child, induce timeout, corrupt identities and validate subsequent reuse. Failure behavior is test evidence; the workbench does not offer a live chaos controller or fabricate recovery screenshots.

## Parity boundary

Pure-feature tests compare all scalar fields and hexadecimal float representations against the original adapter. Complete search tests hash visible/unbounded ranking order, float bits, evidence, decomposition, adequacy, eligible product IDs and compiler decisions. The tests cover serial/process modes and 1/2/4/8 workers where allocated CPUs permit. This supports tested deterministic parity, not an unbounded theorem about all inputs, optional models or changed dependencies.

## Limitations

- Controlled English vocabulary does not understand arbitrary shopping language; unknown terms can yield empty or weak results.
- The speaker repair adds a fact-supported role to the existing vocabulary. It does not interpret subjective `good`/`gud` as measurable quality.
- All exposed catalogues are fictional. Synthetic specification validity and controlled judgments do not establish real-world relevance or customer value.
- The engine scans eligible products. It has no ANN service, persistent inverted index, cluster scheduler, distributed cache or GPU kernel.
- Global fusion, two commerce-ranker calls, constraint gates, parsing and preview compilation remain serial coordinator costs.
- FIFO feature-batch serialization bounds resources but permits queueing and prevents independent batches from fully sharing the pool concurrently.
- Snapshots occupy coordinator and child memory. A 50K catalogue does not imply every query scores 50K candidates after gates.
- Tracing adds overhead. Workbench observations are not experiment samples, latency percentiles or throughput measurements.
- The fresh dependency lock and application were verified on Windows/Python 3.12. Linux/macOS and production hosting need separate verification.
- Original payment, authentication, merchant, voice and Next.js surfaces are outside this release. Inherited optional semantic/provider adapters are not configured or evaluated.
