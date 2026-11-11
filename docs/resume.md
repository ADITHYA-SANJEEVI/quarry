# Resume wording

**Quarry — Deterministic Parallel Commerce Search**

**Stack:** Python 3.12, FastAPI, multiprocessing/ProcessPoolExecutor, RapidFuzz, Pydantic, BM25, character trigrams, reciprocal rank fusion, pytest, HTML/CSS/JavaScript. SQLAlchemy models support inherited domain contracts; the released application needs no database.

- Built natural-language commerce search over reproducible catalogues of up to 50,000 synthetic products using BM25, fuzzy/trigram retrieval and global reciprocal rank fusion, with deterministic budget, availability and exclusion gates.
- Implemented bounded process parallelism with immutable snapshots and complete-result parity checks at 1/2/4/8 workers; validated worker recovery, backpressure and search behavior with 85 automated tests.

Repository: https://github.com/ADITHYA-SANJEEVI/quarry

Do not describe the synthetic catalogue as 50,000 real commercial listings, the partial diagnostic as a completed 36-configuration benchmark, or the historical 46% latency claim as verified performance. Optional provider/model adapters and parent-project Razorpay/Supabase functionality are not part of the released runnable application.
