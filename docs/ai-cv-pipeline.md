# AI / CV Pipeline

End-to-end: CV upload → S3 → text extraction → chunks + embeddings → skills profile → per-job ATS analysis → CV tailoring. Orchestrated by [`cv_service.py`](../app/services/cv_service.py), executed by the `cv_processing` Celery queue, powered by Gemini via [`app/core/ai.py`](../app/core/ai.py), stored via [`app/core/storage.py`](../app/core/storage.py).

## 1. Upload (3-step presigned flow)

```
client                    API                          S3/MinIO
  │  POST /users/me/cv/presign {filename,size,sha256}    │
  │◀── presigned POST url+fields, cv_id ────────────────│   status: pending_upload
  │  POST file directly (NO JWT; fields BEFORE file) ───▶│
  │  POST /users/me/cv/{cv_id}/confirm                   │
  │      API: head_object verify ───────────────────────▶│   status: uploaded → processing
  │◀── CVResponse + process_cv enqueued                  │
```

Rules enforced at presign **and** in the S3 POST policy: `application/pdf` only, ≤ `MAX_CV_SIZE_MB` (5 MB), 15-min upload window. `file_hash` (client-computed SHA-256) is stored and indexed for dedup. A `FOR UPDATE`-guarded transition prevents two concurrent confirms racing the same CV.

**S3 key layout** ([`storage.py`](../app/core/storage.py) `build_s3_key`):
```
cvs/{first-4-hex-of-uuid}/{user_id}/{cv_id}/{filename}
```
The 4-hex prefix gives 65 536 shards for S3 partition performance. Bucket: `jobscout-cvs`. MinIO in dev (`S3_ENDPOINT_URL=http://localhost:9000`), AWS in prod (unset) — identical aioboto3 code. Other storage ops: `generate_presign_download` (1 h TTL), `download_bytes` (workers), `delete_object`, `delete_user_objects` (account-deletion batch purge by prefix).

## 2. Processing — `process_cv` ([tasks.py](../app/workers/tasks.py))

1. Download bytes from S3.
2. **Text extraction**: `pdfplumber`; full text cached on `user_cvs.full_text` (so re-analysis never re-parses the PDF).
3. **Chunking**: `_chunk_text` — 2000 chars per chunk, 200 overlap, paragraph-aware splits; `_detect_section` labels chunks (experience/education/skills/…).
4. **Embeddings**: Gemini `gemini-embedding-001` (3072-d) per chunk, batched; stored as JSONB on `cv_chunks.embedding`. **Skipped gracefully when `GEMINI_API_KEY` is unset** — chunks still stored, embedding null. ⚠️ Nothing consumes these vectors yet (no similarity search / pgvector — [known issue #16](../../docs/known-issues.md)).
5. **Skill extraction** (non-AI, deterministic): `_extract_skills_from_text` keyword-matches against `SKILLS_TAXONOMY` (in `tasks.py`); results upserted into `user_skills` with `pg_insert().on_conflict_do_update(constraint="uq_user_skill")`.
6. Status → `ready` (or `failed` with the error logged; the client sees a sanitized message).

## 3. ATS analysis — `analyze_cv_for_job`

**Cache-first**: `POST /users/me/cv/{cv_id}/analyze {job_id}` checks `cv_analyses` for a row < 24 h old for this (cv, job) — if found, returns it synchronously; otherwise enqueues and returns a `task_id` to poll.

Worker steps (via [`app/core/ai.py`](../app/core/ai.py), model `gemini-2.5-flash`):
1. `extract_keywords_from_jd(job.description)` → structured keyword list (snapshotted to `jd_keywords_snapshot`).
2. `analyze_cv_against_jd(cv.full_text, jd_keywords)` → `match_score` (clamped 0–100), `present_keywords`, `missing_keywords`, `suggested_additions`.
3. Persist to `cv_analyses` with `expires_at = now + 24h` (nightly Beat purge).

## 4. Tailoring — `tailor_cv`

Always async, never cached. Reuses cached `missing_keywords` when a fresh analysis exists (saves one Gemini round-trip). `tailor_cv_section` rewrites the CV summary/skills sections to target the JD with **hard prompt rules against fabricating** experience or skills. Result delivered through task polling; the client renders it copy-paste-ready (backend does not modify the stored CV file).

## 5. Curation & document export (2026-07-11) — `curate_cv` / `generate_cv_document`

Full-CV rewrite with a **human review gate** ([cv_draft_service.py](../app/services/cv_draft_service.py), `cv_drafts` table):

1. `POST /users/me/cv/{cv_id}/curate {job_id}` (AI rate limits apply) → supersedes any live draft for that (cv, job), enqueues `curate_cv`.
2. **Stage 1 — parse**: `parse_cv_structure(full_text)` → structured CV JSON (`CVStructure` schema: contact/summary/skills/experience/education/certifications), **cached on `user_cvs.parsed_structure`** — one parse per CV, reused across every job.
3. **Stage 2 — tailor**: `tailor_cv_full(structure, jd, missing_keywords)` (reuses fresh cached `cv_analyses.missing_keywords`, same as tailor) → full tailored structure + `keywords_injected`; same hard no-fabrication rules, plus "never change employers, titles, dates, degrees".
4. Draft lands in `review` with `content = {original, tailored, keywords_injected}` — the client diffs the two structures; `PATCH` saves user edits to `tailored`.
5. `POST …/approve` (`FOR UPDATE` guard) → `generate_cv_document` renders **DOCX (python-docx) + PDF (fpdf2)** via [docgen.py](../app/core/docgen.py) — one deliberately plain ATS template (single column, standard headings, no tables/images) — to S3 `cvs/{prefix4}/{user}/{cv}/generated/{draft}/` (inside the user prefix, so account-deletion purge covers it).
6. `GET …/download?format=docx|pdf` → presigned GET; 409 until `rendered`.

Malformed LLM output never reaches review: both stages validate through `CVStructure` (defaulted fields), and an effectively-empty tailored CV fails the draft with a sanitized error.

## Gemini integration details — [`app/core/ai.py`](../app/core/ai.py)

| Function | Model | Purpose |
|---|---|---|
| `generate_embedding` / `generate_embeddings_batch` | `gemini-embedding-001` | CV chunk vectors |
| `extract_keywords_from_jd` | `gemini-2.5-flash` | JD → JSON keywords |
| `analyze_cv_against_jd` | `gemini-2.5-flash` | Gap analysis, clamped score |
| `tailor_cv_section` | `gemini-2.5-flash` | Rewrite summary/skills, no-fabrication rules |
| `parse_cv_structure` | `gemini-2.5-flash` | CV text → structured JSON (temp 0, extract-only rules) |
| `tailor_cv_full` | `gemini-2.5-flash` | Full-structure tailoring for document export |

Defensive layers baked in:
- **Input truncation**: CV text capped ~30k chars, JD ~15k before hitting the API (prompt-injection/cost bound).
- **Output parsing**: markdown-fence-stripping JSON parser with fallbacks — a chatty model response degrades, it doesn't crash.
- **Error redaction**: `_sanitize_error` regex strips API keys from any surfaced error; structlog redacts them from logs too.
- **Token caps**: `gemini_max_tokens_analysis` (1500) / `gemini_max_tokens_tailor` (2000) from [config.py](../app/core/config.py).
- **No key, no crash**: dev without `GEMINI_API_KEY` skips embeddings and fails AI endpoints cleanly; production **refuses to boot** without a key.

## Cost & abuse controls

| Control | Value | Enforced at |
|---|---|---|
| Presign rate | 3/hour/user | route decorator |
| Analyze/tailor rate | 10/hour/user | route decorator |
| Daily AI cap | 50/day/user | Redis counter (`check_ai_daily_cap`) → 429 |
| Task poll rate | 60/min | route decorator |
| Analysis cache | 24 h TTL | `cv_service` cache-first read |
| Input truncation | 30k/15k chars | `ai.py` |

## Client integration contract

Both frontends implement the same loop (dashboard: [use-cv.ts](../../Dashboard-Job-Hunter/src/hooks/use-cv.ts) React Query `refetchInterval: 2000`; Flutter: await-loop in [job_detail_screen.dart](../../App-Job-Hunter/my_flutter_app/lib/features/jobs/job_detail_screen.dart)):

1. `POST …/analyze` → if response contains a cached result, render immediately; else take `task_id`.
2. Poll `GET …/tasks/{task_id}` every 2 s until `success`/`failure` (Flutter times out at 60 s).
3. Handle **429** (hourly or daily cap) with a friendly message — don't retry-loop into the limiter.
