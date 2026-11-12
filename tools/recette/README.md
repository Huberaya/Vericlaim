# Recette qualification harness

Reusable, disposable tooling that proves the VeriClaim data plane works with
**real components**, not mocks. See `docs/infrastructure/` for the results.

```bash
# from the repository root, in a Python 3.11+ environment
pip install -r backend/requirements.txt "pgserver>=0.1.4" "moto[server]>=5.0"
python -m tools.recette.run            # full journey, writes .recette/last-run.json
```

| Module | Role | Real? |
|---|---|---|
| `stack.py` | PostgreSQL 16 + the reviewed `infra/neon/0*.sql` role/grants scripts, migrated by the real Alembic chain | **real** |
| `stack.py` | S3 API used through the application's own `S3ObjectStorage` (boto3, SigV4, presigned URLs) | real protocol, local server |
| `oidc_stub.py` | OpenID Provider the real `OidcClient` performs discovery/JWKS/PKCE against | simulated IdP |
| `clamd_stub.py` | clamd INSTREAM wire protocol | **not an antivirus engine** |
| `e2e_recette.py` | Uvicorn-served application, two tenants, workers as real OS processes | **real application** |

Nothing here contacts Neon, Vercel, Clerk or any production resource. Everything
lives under the git-ignored `.recette/` tree and is disposable.

**Never cite a `clamd_stub` verdict as an antivirus proof.** The module docstring
and `docs/infrastructure/06-antivirus-et-fichiers.md` say exactly what it cannot do.
