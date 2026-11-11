# Publication audit

The public source is a new reviewed snapshot. The parent workspace's Git objects, environment files and local application state are not its ancestry. The owner authorized publication on successful checks; no GitHub Actions workflows are included or requested.

## Boundary review

The extraction followed static import dependencies from the existing HPC engine and deterministic parser. Only source, authored fictional test fixtures, small catalogue manifests, documentation, lock files and reviewed workbench screenshots belong to the release. The original Next.js/media/payment/authentication application was excluded. Retained ORM models are types, not a database export. The release application mounts no payment mutations and uses no database connection. Provider adapters remain disabled.

The original checkout contains ignored environment and generated/private artifact locations. Their contents were not copied, logged or published. Its history was deliberately excluded; this report does not certify that private history as secret-free. No credential refresh or extra OAuth workflow scope was needed.

## Scanning method

Gitleaks **8.30.1** was obtained from the official `gitleaks/gitleaks` release. Its Windows x64 archive was verified against the published SHA-256 checksum (`d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e`). The executable and download archive remain outside the public repository.

The staged source was exported with `git checkout-index` and scanned using `gitleaks dir --redact`. This scans the actual candidate files without including the virtual environment or generated caches. Source review additionally checked environment loading, optional API/model credentials, Razorpay references, external URLs/assets, local file/database paths, and the exposed application routers. Final local commits are scanned with `gitleaks git --redact --log-opts=--all` before push. Scan summaries are retained in release evidence; secret values are never printed.

Explicit tree checks reject workflow files, non-example environment files, generated JSONL, database files, archives, caches, private-key material and local receipt/log paths. Screenshot review checks actual application content, not prior private presentation captures. PNGs contain only fictional product results and process observations.

## Licensing and ownership

The parent history records the owner as its author. The user identifies the source as their project; self-authored Aastrika search lineage is documented in provenance. No third-party source bundle, marketplace dataset, pretrained model or external product image was selected. Fictional fixtures and the deterministic generator are authored project material. Installed dependencies retain their separate notices, documented in `THIRD_PARTY_NOTICES.md` and the distribution metadata inventory.

These checks establish the reviewed publication boundary and absence of known detected secrets in that tree. They are not a guarantee against every possible secret pattern or a production security certification. Bind the application to loopback. Public multi-user service deployment and optional external provider configuration require a separate security review.
