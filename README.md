# Project Evidence Agent

A grounded document-analysis system for software project delivery. It ingests related PDF, DOCX, and TXT evidence, extracts structured project facts, detects contradictions, evaluates deterministic checklist rules, pauses for item-level human review, and maintains a provenance-backed Project Status Brief.

This repository implements Task 1 as a modular monolith using React, FastAPI, LangGraph, PostgreSQL/pgvector, a deterministic fake provider, and one optional hosted OpenAI provider. Uploaded source documents are immutable; human decisions update only the derived brief.

## What it demonstrates

- Valid PDF, DOCX, and TXT parsing with source document, page/section, span, and exact evidence provenance.
- One combined classification-and-extraction call per newly processed document.
- Corpus-scoped canonical entities with exact-first and vector-assisted candidate retrieval.
- Deterministic grounding, normalization, conflict detection, rules, budgets, and commits.
- Durable PostgreSQL LangGraph checkpoints and resume by the same `run_id`.
- Initial and incremental workflows that avoid reprocessing unaffected documents and report items.
- Item-level fact inclusion, conflict resolution, finding review, and incremental update decisions.
- Human-approved committed-state semantics: selected conflict values become current values; unresolved conflicts remain unresolved; excluded/rejected items remain audit-only.
- REST, React, and MCP transports over the same application and workflow implementation.
- Deterministic no-key Atlas and Beacon test/demo corpora plus an opt-in hosted provider.

## Quick start: deterministic demo

Prerequisites: Docker Desktop with Compose and `make`. No local Python, Node.js, PostgreSQL, API key, or paid service is required.

```bash
make demo
```

Open [http://localhost:3000/?demo=1](http://localhost:3000/?demo=1). The first build may take a few minutes while Docker downloads dependencies.

Stop the application without deleting its PostgreSQL volume:

```bash
make down
```

`make demo` always selects `FakeLLMProvider`, even when a local `.env` selects hosted inference.

### Atlas walkthrough

1. Select **Start fresh Atlas demo**. The application creates a new isolated corpus containing eight realistic project documents across PDF, DOCX, and TXT.
2. Open **Analysis Run** to inspect durable stages, provider/model identity, calls, tokens, cost, and warnings.
3. Open **Extracted Info** to inspect grounded facts and exact provenance. Normal facts may be individually included or excluded without altering source evidence.
4. Open **Draft Brief** to preview the uncommitted brief assembled from included, non-conflicting facts.
5. Open **Review**. Deterministic Atlas produces one production-launch conflict and one unowned-high-risk finding. Choose either conflicting source value, keep it unresolved, or exclude it.
6. Select **Commit reviewed brief** and inspect **Status Brief**. It shows the current human-approved state; conflicting evidence and decisions remain available in collapsed audit detail.
7. In **Corpus**, select **Add Atlas weekly update**. The new change request starts a focused incremental Analysis Run.
8. Review the beta-date change, vendor-risk ownership/state change, and new pilot decision. Unrelated brief content remains unchanged.

Atlas source documents contain natural workplace prose, irrelevant material, tentative proposals, repeated supporting evidence, and a prompt-injection fixture. The deterministic oracle is separate in `backend/src/project_evidence/atlas_ground_truth.py`; documents contain no synthetic `FACT` grammar.

## Development and tests

Developer prerequisites: Docker Desktop, `make`, Python 3.11+, Node.js, npm, and [uv](https://docs.astral.sh/uv/).

```bash
make install
make test
```

`make test` forces `FakeLLMProvider`, requires no API key, and exercises PostgreSQL/pgvector plus the PostgreSQL LangGraph checkpointer. The current deterministic result is **48 passed, 1 skipped**. The skipped test is the opt-in paid OpenAI smoke test.

Frontend production build:

```bash
cd frontend
npm run build
```

Useful commands:

```bash
make api       # FastAPI at http://localhost:8000; OpenAPI docs at /docs
make frontend  # Vite development server
make app       # production-style Docker Compose stack
make mcp       # MCP over standard input/output
make mcp-http  # local Streamable HTTP MCP server on port 8001
make down      # stop Compose without deleting PostgreSQL data
```

## Hosted inference (optional)

Fake mode is the default for tests and demos. The hosted implementation uses OpenAI Responses API Structured Outputs with `gpt-4o-mini-2024-07-18`. Each newly processed document receives one request that returns its document type and grounded claim candidates; the application never sends the entire corpus as one prompt.

Create local configuration without placing a secret in shell history:

```bash
cp .env.example .env
```

Edit `.env` and set:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your-key-here
```

Then run `make app`. `.env` is ignored by Git and must never be committed. Provider failures are sanitized, persisted with actionable retry guidance, and resumable from durable checkpoints. Hosted inference was exercised during development; deterministic tests remain the authoritative repeatable suite because live output varies and incurs cost.

The paid single-document smoke test is explicitly opt-in:

```bash
make test-live-llm
```

Do not use it for routine testing. Embeddings remain local and deterministic by default, including during hosted extraction. Optional vector results supply corpus-scoped candidates only; deterministic reconciliation remains authoritative.

## Product workflow

```text
Corpus
  → Analysis Run
  → Extracted Info
  → Draft Brief
  → Review
  → Status Brief

Later evidence
  → Incremental Analysis Run
  → Extracted Info
  → Review proposed updates
  → Updated Status Brief
```

An Analysis Run is one durable graph execution against one corpus. PostgreSQL checkpoints use `thread_id = run_id`. Duplicate bytes within a corpus are reused by SHA-256, and incremental runs process only newly arrived documents.

## Architecture

- `backend/src/project_evidence/api.py` — thin REST routes for corpora, rules, documents, runs, evidence, review, drafts, reports, and metrics.
- `backend/src/project_evidence/workflow.py` — LangGraph routing, validation/retry, affected scope, review interrupt, and structural commit.
- `backend/src/project_evidence/parsing.py` — PDF, DOCX, and TXT parsing; OCR is intentionally out of scope.
- `backend/src/project_evidence/llm.py` — minimal `LLMProvider` protocol and deterministic `FakeLLMProvider`.
- `backend/src/project_evidence/hosted_llm.py` — OpenAI Structured Outputs adapter with trusted/untrusted prompt separation and strict conversion.
- `backend/src/project_evidence/embeddings.py` — local/optional hosted embedding abstraction and candidate retrieval support.
- `backend/src/project_evidence/rules.py` — validated JSON rulesets and deterministic condition evaluation.
- `backend/src/project_evidence/models.py` — compact corpus-scoped PostgreSQL schema with JSONB and pgvector.
- `backend/src/project_evidence/mcp_server.py` — thin MCP transport over the existing application behavior.
- `frontend/` — minimal React/TypeScript reviewer interface using REST.
- `demo_data/` — realistic Atlas and distinct Beacon evidence fixtures.

Application code—not the model—owns IDs, hashes, provenance validation, canonical reconciliation authority, conflicts, rules, call/cost guards, review decisions, stale-write protection, and report writes.

### Canonical and committed state

Grounded claims reconcile into corpus-scoped canonical entities with stable IDs, aliases, typed fields, supporting claim IDs, provenance, and history. Matching uses exact normalized identity and learned aliases first. Pgvector supplies bounded candidates when wording differs; it never decides equivalence or conflict.

A conflict requires the same canonical entity, the same fact dimension, and materially different normalized values. Repeated equivalent claims add support, while compatible fields enrich the existing entity. Formal change requests generate proposed updates without mutating committed state before approval.

- Selecting a conflict source commits that value as current content. Original claims and the human resolution remain under **Resolution & conflicting evidence** and in audit history.
- Keeping a conflict unresolved shows every contradictory value and source as unresolved.
- Excluding a conflict or rejecting a finding removes it from active content while retaining its decision/evidence in audit history.
- Approved rule findings appear as acknowledged findings.
- Evidence-validation issues can never become factual brief content.

## REST API

Interactive OpenAPI documentation is at [http://localhost:8000/docs](http://localhost:8000/docs).

```text
POST /api/corpora
GET  /api/corpora
GET/PUT /api/corpora/{corpus_id}/ruleset
POST /api/corpora/{corpus_id}/documents
GET  /api/corpora/{corpus_id}/documents
POST /api/corpora/{corpus_id}/runs
GET  /api/corpora/{corpus_id}/runs
GET  /api/runs/{run_id}
POST /api/runs/{run_id}/resume
GET  /api/runs/{run_id}/claims
POST /api/runs/{run_id}/claims/{claim_id}/inclusion
GET  /api/runs/{run_id}/draft
GET  /api/runs/{run_id}/review-items
POST /api/runs/{run_id}/review
GET  /api/runs/{run_id}/metrics
GET  /api/corpora/{corpus_id}/report
```

## Rules and limits

Each corpus owns a small JSON ruleset. Built-in examples are `unowned_high_risk`, `overdue_milestone`, and `red_project_status`. Rules may be enabled, disabled, or supplied through REST, MCP, or the Corpus UI using `eq`, `missing`, `not_in`, and `before_today`. This is deliberately not a general-purpose rule language.

```env
MAX_RETRIES_PER_STAGE=1
MAX_LLM_CALLS_PER_RUN=16
MAX_RUN_COST_USD=0.25
```

Every stage records status, latency, calls, tokens, estimated cost, and sanitized errors.

## MCP

MCP exposes existing application capabilities rather than duplicating workflow logic.

```bash
make install
make db
make mcp       # stdio
make mcp-http  # http://127.0.0.1:8001/mcp
```

Tools cover corpus creation/selection, rules, document upload/listing, initial and incremental analysis, run inspection, evidence, fact inclusion, review decisions, resume, and draft/committed briefs.

Typical flow: `create_corpus` → `add_document` → `start_analysis` → inspect evidence/draft/review → persist inclusion and review decisions → `resume_analysis` → `get_committed_brief`. Later documents use the same focused incremental path.

## Known limitations and non-goals

- Fake inference is fixture-specific deterministic behavior, not general NLP.
- Workflow execution is synchronous in the API process. The local proxy allows five minutes for hosted multi-document analysis, but there is no worker queue or live stage streaming.
- A process death before a provider response is durably committed may repeat that in-flight call; completed calls are not repeated.
- Schema setup uses idempotent startup creation plus a small compatibility alteration rather than a migration framework.
- TXT provenance has stable section/span information but no native page number.
- The UI is a single-user reviewer surface using browser local storage, not an authenticated multi-user history dashboard.
- OCR, authentication, deployment, workers/queues, arbitrary domains, and additional hosted providers are out of scope.

## Publish safely with GitHub CLI

Never upload the raw working directory as a ZIP: local dependencies, caches, and `.env` may exist even though Git ignores them.

```bash
git init
git add .
git status --short
git check-ignore -v .env backend/.venv frontend/node_modules frontend/dist work
git diff --cached --check -- ':!*.pdf' ':!*.docx'
git commit -m "Implement grounded project evidence agent"

gh auth login
gh repo create doctask-your-name --private --source=. --remote=origin --push
```

Before committing, verify that `.env`, virtual environments, `node_modules`, `dist`, `work`, editor files, and caches are absent from `git status`. If a secret was ever committed or uploaded, revoke it before pushing; `.gitignore` does not remove secrets from existing Git history.

No license is included. Keep the repository private for a take-home submission, or add an appropriate license before intentionally publishing it as open source.
