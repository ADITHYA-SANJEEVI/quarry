# Release verification

Release date: **11 November 2026**. This is an extracted HPC source snapshot, not the complete Razorpay Buildathon application. The parent working tree and history remain separate.

## Reproduction boundary

A new Python 3.12 virtual environment was created inside the snapshot and installed from explicit pinned dependencies. `requirements-lock.txt` records the complete resolved environment. The deterministic generator regenerated all three catalogues; their SHA-256 values match the retained original manifests:

| Products | Available | JSONL SHA-256 |
|---:|---:|---|
| 1,000 | 926 | `45376c57a8e04b3acefcf2d59155b53cd8928b378caf2852e04ed2d3f11c8ee5` |
| 10,000 | 9,272 | `0afa8eec243db5841182b4d69af8bae4ce26c9ddb34c4938527c84c0c011c772` |
| 50,000 | 46,357 | `6bbc5142e1efffa3046ab85b03f0e461c227b909fcedf46aca41e1d30859b3f7` |

Generation reads no live database and downloads no product data. JSONL files remain ignored and are regenerated after checkout.

## Meaningful verification

The suite includes original scalar-feature, catalogue, experiment, FIFO/admission, worker-failure and corruption tests, plus release-specific complete-digest parity and HTTP tests. Full-result parity is checked with worker counts 1/2/4/8 on allocated CPUs. A real worker is terminated by a test; the runtime invalidates its pool and later recovers. Timeout cleanup targets owned children.

**Final prepublication result: 85 passed, zero failures, one upstream HTTPX deprecation warning in 175.65 seconds.** `python -m pip check` reported no broken requirements. `docs/evidence/tests.xml` records the suite outcome with host-identifying attributes removed. Initial attempts encountered an unavailable temporary directory and one newly exposed negative-role exclusion defect. Test storage was isolated under ignored `backend/artifacts`; the defect was fixed and the suite rerun. The focused release suite passed all ten cases after that fix. The test client's upstream HTTPX deprecation warning does not change search results.

The first staged-source export passed Gitleaks 8.30.1 with no detected leaks. Browser verification passed in Chromium 156.0.8078.4: actual constrained results, two-worker parallel execution, no JavaScript console errors, and no horizontal overflow at a 390px mobile viewport. Both 1440px-wide screenshots were reviewed at native resolution. Raw QA metadata is in `docs/evidence/browser-qa.json`. Final staged-source and local Git-object scans are required before push. Remote clean-clone reproduction is recorded after publication; this first-release record does not predate or assume that remote result.
