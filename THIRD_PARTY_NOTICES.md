# Third-party notices

The MIT licence at the repository root covers the author's source, authored synthetic fixtures/generator, documentation, and release workbench. Installed dependencies retain their respective licences; no dependency distribution, pretrained model, product photography, or external dataset is vendored here.

The tested environment is pinned in `requirements-lock.txt`. The table below records distribution licence metadata from the fresh release environment. Consult each installed distribution's licence and bundled notices for authoritative terms, including data-file notices. `docs/dependency-licenses.json` preserves project links.

| Dependency | Version | Licence metadata |
|---|---|---|
| annotated-doc | 0.0.6 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| certifi | 2026.10.23 | MPL-2.0 |
| click | 8.5.1 | BSD-3-Clause |
| colorama | 0.4.6 | See distribution notices |
| fastapi | 0.141.1 | MIT |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| iniconfig | 2.3.1 | MIT |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.7.0 | MIT |
| psutil | 7.0.0 | BSD-3-Clause |
| pydantic | 2.13.4 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| pydantic_core | 2.46.4 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pytest | 8.4.2 | MIT |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| RapidFuzz | 3.14.5 | MIT |
| SQLAlchemy | 2.0.52 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| symspellpy | 6.10.0 | MIT License |
| typing-inspection | 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.52.4 | BSD-3-Clause |

The source retains references to optional spaCy and sentence-transformers integrations. Those packages/models are not installed by the release requirements, redistributed, or evaluated. Enabling them requires reviewing their package and individual model licences. SymSpellPy is installed as a separately licensed dependency; its packaged resources are not copied into the repository.

BM25, reciprocal rank fusion and character-trigram similarity are algorithm descriptions, not third-party datasets. Razorpay and Supabase names describe the parent project lineage; their SDKs, credentials, logos, account records and payment assets are not bundled.
