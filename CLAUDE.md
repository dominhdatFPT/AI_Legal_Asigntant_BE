# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Backend for **Legal Assistant AI**, a Vietnamese legal-help web app (AI Q&A with legal citations, administrative procedures, contract templates, document upload/OCR, lawyer consultations). It is a set of Python/FastAPI microservices behind an Nginx gateway. Docs, commit messages and user-facing error `message` strings are in **Vietnamese**; keep it that way.

Read these before non-trivial changes. They are the team's binding conventions:
- `docs/API_GUIDELINES.md`: response format, error codes, auth, timeouts/retry, uploads, WebSocket, OpenAPI and Git rules. Text marked "hiện có" describes code that exists; everything else is a required convention for new code.
- `TONG_QUAN_DU_AN.md`: project overview for newcomers.
- `README-GEMINI.md` (agent-service + Gemini) and `README-RAG.md` (rag-service embedding/pgvector/chunking).
- Each `services/<name>-service/README.md` and `docs/api/<SERVICE>_*.md` for per-service status.

## Commands

All commands run from the repo root. The shell is PowerShell on Windows.

```bash
python -m pip install -r requirements-test.txt     # every service's deps + pytest + editable libs/common_auth

# What CI runs (.gitlab-ci.yml, python:3.12)
pytest tests libs/common_auth services/user-service/tests -q

# Single file / single test
pytest tests/test_api.py -q
pytest "tests/test_api.py::test_health[user-users]" -q
pytest services/user-service/tests/test_security.py -k refresh -q

# Service-local suites that CI does not run: invoke each separately (see "Test isolation")
pytest services/agent-service/tests -q
pytest services/rag-service/tests -q
```

Run a service locally (from its directory, using the root `.env`):
```bash
cd services/<name>-service
python -m uvicorn app.main:app --reload --port 800x --env-file ../../.env
```

Docker (needs Compose ≥ 2.17 for `additional_contexts`):
```bash
docker compose up -d --build <service>
docker compose exec -T gateway nginx -t && docker compose exec -T gateway nginx -s reload   # reload gateway config without restarting services
```
Extra compose files are opt-in overlays: `docker-compose.document-worker.yml` (document worker + ClamAV), `docker-compose.document-notifications.yml`, and `docker-compose.jwt-local.yml` (exposes contract/notification on direct ports 8006/8009).

Dev helpers: `python scripts/mint_dev_token.py --role lawyer` signs a dev access token (needs `JWT_PRIVATE_KEY_PATH`). `python scripts/check_contract_notification_jwt.py [--gateway-url http://localhost:8080]` runs read-only HTTP checks against running containers.

## Architecture

**Routing.** Frontend → Gateway (`gateway/nginx.conf`, `localhost:8080`) → service on internal port 8000. The gateway rewrites `/api/<resource>/...` to `/api/v1/<resource>/...`, generates `X-Request-ID`, and owns CORS. Services must not configure CORS themselves. `/internal/v1/...` is never routed publicly. Services call each other directly over the Docker network using `*_SERVICE_URL` env vars.

**Data ownership.** Each service owns its own Supabase PostgreSQL database (`<NAME>_DATABASE_URL`). A service never reads another service's DB and never creates cross-DB foreign keys. It stores bare IDs such as `owner_user_id` and verifies them via API or the JWT. Use soft delete (`status`, `deleted_at`) for anything other services reference. Schema lives in `services/<name>-service/database/NNN_*.sql`: numbered, idempotent SQL applied in the Supabase SQL editor, each paired with a `check_*_db.sql` verification script. The `alembic/` folders are placeholders. Root `database/` holds conventions only.

**Service layout.** Each service is a separate `app/` package: `main.py` (FastAPI app, error handlers, routers), `api/` (routers, `deps.py`), `core/` (config, database, security, responses), `models/` (SQLAlchemy), `schemas/` (Pydantic), `repositories/` (queries), `services/` (business logic, outbound clients). Each also has its own `requirements.txt`, `Dockerfile`, and `openapi.yaml`. Maturity varies a lot: user, agent, rag and document are implemented, while lawyer, consultation, notification and location are mostly skeletons with health checks.

**Auth.** user-service verifies a Firebase ID token and issues the system's own RS256 access and refresh tokens (also as HttpOnly cookies). Every other service verifies tokens locally with the public key through `libs/common_auth` (`get_current_user`, `require_role("lawyer")`, `add_auth_error_handler`) and never calls user-service per request. The resource owner always comes from the token's `sub`, never from a client-sent `user_id` or an `X-User-Id` header. For service-to-service calls, forward the user's `Authorization: Bearer` when acting on the user's behalf. System calls go to `/internal/v1/...` with `X-Internal-Token: $INTERNAL_TOKEN`, compared with `hmac.compare_digest`. Dockerfiles install `common_auth` via `COPY --from=libs`, so new services need the `additional_contexts: libs: ./libs` entry in compose.

**AI path.** agent-service `POST /agent/conversations/{id}/messages` → rag-service `POST /internal/v1/rag/search` (Gemini `gemini-embedding-001`, 768-dim, L2-normalized; pgvector HNSW cosine; chunks split by Chương/Điều/Khoản/Điểm, max 2,500 chars) → Gemini via `google-genai` → persist the Q&A. If RAG or the LLM fails, the user's question is not saved. Embedders sit behind an `Embedder` interface with a `FakeEmbedder` for tests.

## Conventions enforced by tests or review

- **Response envelope:** `{"data": ..., "error": null | {"code", "message", "details"?}, "request_id": "..."}`, with lists in `data.items` plus `data.pagination {page, page_size, total, total_pages}`. Health endpoints are the exception: `GET /api/v1/<prefix>/health` returns exactly `{"status":"ok","service":"<name>-service"}` (checked in `tests/test_api.py`). Validation errors are `422 VALIDATION_ERROR`.
- **Upstream failures:** return `503 <SERVICE>_UNAVAILABLE`, `504 <SERVICE>_TIMEOUT`, or `502 <SERVICE>_BAD_RESPONSE`. An upstream 4xx is a business result, so do not turn it into a 5xx. Timeouts are 5s for normal calls and 60s for LLM/embedding/OCR. Retry only GET or explicitly idempotent calls, at most twice. Never fabricate a success.
- **Contract first:** every service has one `openapi.yaml` (OpenAPI 3.0.3), and every operation needs `x-status: implemented | partial | planned` plus examples. `tests/test_openapi.py` validates all of them, so update the contract in the same change as the code. Cross-service payload samples live in `contracts/`.
- JSON fields use `snake_case`, with `is_`/`has_` for booleans. Use UUID ids, UTC ISO-8601 timestamps, money as integer đồng, and lowercase snake_case enums. Missing values are returned as `null`, not omitted.
- Do not log tokens, passwords, contract content, identity documents, or chat text.

## Test isolation (important)

Every service uses the same top-level package name `app`, so one pytest process can only hold one service's `app` at a time. Root `tests/test_api.py::load()` imports a service's `app/main.py` by path and then **purges `app*` from `sys.modules`**. Keep that cleanup when adding loaders, or service-local suites such as `services/user-service/tests` will import the wrong `app`. Service-local `conftest.py` files put the service root on `sys.path` and use in-memory SQLite (`StaticPool`) instead of Supabase. JWT tests use an RSA key pair generated in memory (`tests/conftest.py`, fixture `service_auth`). Tests must not need network access, Supabase, or a real Gemini key. Real-pgvector tests are skipped unless `RAG_TEST_DATABASE_URL` is set, and they wipe their tables.

Per the guidelines, a new API needs tests for success, 422, 401/403 (or 404 for non-owner) when auth applies, and 502/503/504 when it calls another service.

## Git

GitLab with merge requests and at least one reviewer. The integration branch is actually `develop` (the guidelines say `dev`). Feature branches use `feature/<name>` or `fix/<name>`. Commit format is `<type>: <mô tả>` or `<type>(<service>): ...`, with type one of `feat|fix|docs|test|refactor|chore`. Never commit `.env`, `*.pem` (keys stay outside the repo via `JWT_KEYS_DIR` / `JWT_*_KEY_PATH`), or real personal data.
