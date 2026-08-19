# Project Evidence Agent — Progress

## Current status

- Status: **Focused post-live correctness/usability pass complete; awaiting review.**
- `TASK.md` was read completely (686 lines) before implementation.
- Verification is recorded in the latest report at the end of this file.
- The backend runs end to end with FastAPI, PostgreSQL/pgvector, `FakeLLMProvider`, conditional LangGraph routing, item-level review interrupts, structural report commits, and PostgreSQL checkpoint/resume.
- Restart and crash-window recovery are proven by reconstructing the application, provider, graph, database pool, and checkpointer, then resuming the same `run_id`; durable per-document completion markers prevent repeated completed LLM stages.
- Incremental affected-scope processing, concurrency isolation, report write protection, configurable deterministic rules, stage metrics, budget guards, and graceful provider recovery are implemented and tested.
- One OpenAI hosted provider is implemented behind the existing abstraction; `FakeLLMProvider` remains the default.
- A minimal React interface, Docker Compose demo, and thin MCP transport are implemented. Deployment, authentication, and optional functionality remain deferred.

## Phase 1 implementation report

### What is implemented

- FastAPI application with the required corpus, upload, run, resume, review, report, and metrics endpoints, plus a claims inspection endpoint and health endpoint.
- PostgreSQL/pgvector foundation using SQLAlchemy async sessions and automatic Phase 1 table creation.
- Compact Phase 1 schema: documents keep parsed source spans in JSONB; conflicts keep claim/source snapshots in JSONB; reports are structural JSONB; separate tables exist only for claims, conflicts, findings, proposed changes, and independently decidable review items.
- PDF, DOCX, and TXT parsing with page/section-aware stable source spans; no OCR.
- Async `LLMProvider` protocol and deterministic, no-network `FakeLLMProvider`.
- Project Atlas synthetic corpus with conflicting launch dates, normal milestones/owners/decisions/status, a high-severity unowned risk, and an embedded prompt-injection instruction.
- LangGraph workflow: prepare → parse → classify → extract → validate, with conditional retry or escalation; then conflict detection → deterministic rule evaluation → structural proposals → persisted item review → interrupt → resume → approved-only commit.
- Mechanical grounding verifies document ID, filename, span index, and exact evidence text against stored source content.
- Deterministic normalized-value conflict detection preserves both claims and both provenance objects and never chooses a conflicting value.
- Deterministic `unowned_high_risk` rule.
- Independent persisted approval/rejection for conflicts, findings, and proposed report changes. Only approved proposed changes mutate the report.
- Structural Project Status Brief containing status, milestones, deadlines, owners, risks, decisions, conflicts, findings, and recent changes, with provenance on every factual item.
- PostgreSQL-backed LangGraph checkpoints keyed by `thread_id = run_id`; review pauses survive full application reconstruction.
- SHA-256 duplicate-document reuse at upload time (basic idempotency included because it was inexpensive and protects Phase 1 behavior).
- Local PostgreSQL/pgvector Compose service and small Makefile entry points for setup, tests, and API startup. No application deployment configuration was added.

### Implementation decisions made for speed

1. Source spans are JSONB on immutable document rows instead of a `source_span` table.
2. Conflict membership and provenance are JSONB snapshots instead of join tables.
3. The report is one structural JSONB value instead of report-section and revision tables.
4. Run stages and metrics are JSONB on the run row instead of separate stage-execution records.
5. Database tables are created at application startup for Phase 1; a migration framework is deferred.
6. The fake provider maps fingerprints of natural-language fixture sentences to deterministic structured outputs. Business logic is not hardcoded to Atlas names or dates.
7. Execution stays in the FastAPI process. Durable checkpoints provide the required pause/restart behavior without workers or queues.

### Tests and verified results

`make test` starts/health-checks PostgreSQL and runs 8 no-key tests:

- TXT, DOCX, and PDF source-span parsing.
- Full Atlas run with seven grounded claims.
- Conflict detection retaining both launch dates and sources.
- High-severity unowned-risk finding.
- Prompt-injection text ignored as workflow instructions and not extracted as a claim.
- Item-level review, a pending-review re-interrupt, approved report commits, and rejected non-change independence.
- Rejected proposed changes never entering the report.
- Duplicate upload reuse by SHA-256.
- Full application restart followed by same-`run_id` PostgreSQL checkpoint resume, with zero calls to the replacement fake provider.
- Invalid grounding taking the real retry branch, exhausting one retry, and taking the escalation branch; ungrounded claims remain uncommittable.

Latest result: **8 passed in 4.38s** through `make test`. A separate compile check also passed.

### Known Phase 1 limitations

- The fake provider recognizes the synthetic `FACT` fixture grammar; it does not attempt general natural-language intelligence.
- Only the `unowned_high_risk` checklist rule is implemented. `overdue_milestone` and configurable rule loading remain Phase 2.
- Calls/tokens are summarized at run completion and stages are logged, but latency, per-stage token/cost aggregation, budget guards, and graceful provider-failure records remain Phase 2.
- Basic upload deduplication and checkpoint replay are present, but crash windows inside nodes and comprehensive idempotent re-entry are not yet hardened.
- Runs analyze explicitly selected documents as a complete initial batch. Affected-scope incremental updates and unchanged-section guarantees are not implemented.
- Corpus/run keys and scoped endpoint queries are present, but simultaneous-run isolation has not yet received its required concurrency test.
- PostgreSQL tables are startup-created rather than migration-managed.
- Conflict and finding approvals are independently persisted. As decided in the architecture, their report representation still flows through separately reviewed proposed changes.
- There is no hosted provider, React UI, authentication, MCP, worker queue, or deployment configuration.

### Remaining Phase 2 work

- Exhaustive crash-window and completed-stage idempotent replay behavior/tests.
- Incremental ingestion, affected fact/entity scope, relevant prior-claim retrieval, targeted rules/proposals, and unaffected-section preservation tests.
- Stronger prompt construction/escaping tests for a future hosted provider.
- Concurrent corpus/run isolation test and same-corpus report write conflict handling.
- Per-stage latency, call/token/cost/error metrics and configurable call/retry/cost enforcement.
- Graceful provider/system error persistence and retry guidance.
- Remaining required test matrix: clean zero-conflict corpus, second corpus, budget failure, concurrency, incrementality, and broader resume/idempotency cases.

The list above records the Phase 1 handoff and is now completed by the Phase 2 work below.

## Phase 2 implementation report

### What is implemented

- Durable per-document `classify` and `extract` completion markers in run metrics. Re-entry skips completed provider calls.
- Extracted claims and their completed-extraction marker commit in one PostgreSQL transaction, closing the crash window between durable domain output and completed-work bookkeeping.
- PostgreSQL checkpoint recovery still uses `thread_id = run_id`; a test reconstructs the whole application and explicitly replays extraction before resuming to prove no replacement-provider calls occur.
- Incremental runs accept `kind = "incremental"`, process only previously unprocessed documents, and reject explicitly selected already-processed documents without model calls.
- `compute_affected_scope` derives exact `fact_type:entity_key` dependencies from new grounded claims.
- Conflict retrieval/comparison and rule evaluation are restricted to affected scope while still loading relevant prior grounded claims from the same corpus.
- Proposed changes are restricted to affected sections/items. Approved commits replace only the addressed structural item; unrelated report sections remain structurally identical.
- Project Atlas now includes `weekly_status_02.txt`, which adds one decision and proves only the decisions section changes.
- Project Beacon provides distinct people, dates, wording, milestones, risks, and status; it produces zero conflicts.
- Configurable deterministic corpus rulesets, with `unowned_high_risk`, `overdue_milestone`, and `red_project_status` defaults.
- Every stage event records status, latency, LLM calls, input/output tokens, estimated cost, and error.
- Durable run totals record calls, tokens, cost, completed documents, run kind, affected scope, and base report version.
- Call and cost guards route to a persisted failed state and LangGraph retry interrupt. Errors include the failed stage, likely reason, retryability, and user action.
- Provider failures preserve completed work and can resume at the failed stage. The recovery test proves completed classification is not repeated.
- Independent corpora run concurrently without mixing document IDs, claims, or workflow state.
- Reports carry a minimal integer version. `SELECT ... FOR UPDATE` plus the captured base version prevents a stale same-corpus run from overwriting a newer report.
- Approved changes revalidate all provenance against the current document/span before commit.

### Minimal schema decision

Phase 2 adds only `reports.version`. Completed-stage records, base report version, affected scope, and usage remain in the existing run JSONB metrics. No stage table, report revision table, source-span table, join-table expansion, queue, cache, or worker infrastructure was added.

### Tests and verified results

`make test` starts and health-checks PostgreSQL/pgvector and runs **16 no-key tests**. In addition to the Phase 1 coverage, Phase 2 verifies:

- New-document-only incremental model calls.
- Exact affected scope and targeted decision-only proposals.
- Structural preservation of all unrelated report sections.
- Clean second corpus with zero conflicts.
- Concurrent independent corpora with disjoint document/claim provenance.
- Graceful call-budget failure with completed work and actionable error retained.
- Provider outage recovery without repeating completed classification.
- Same-corpus stale-write rejection.
- Duplicate processed document rejection without additional provider calls.
- Configurable rule disablement.
- Application-restart and explicit completed-extraction replay without provider calls.
- Atomic extraction output/completion bookkeeping.

Latest verified result: **16 passed in 6.82s** through the exact reviewer command `make test`.

### Known limitations after Phase 2

- Fake inference still uses the transparent synthetic `FACT` fixture grammar. Natural-language inference belongs to Phase 3.
- Incremental candidate retrieval uses exact normalized entity keys first, then corpus-scoped pgvector similarity for candidates; deterministic entity equivalence remains authoritative.
- A provider call whose process dies before any result can be committed may be retried. Once result/domain output is durably committed, the call is not repeated.
- Rule configuration is an environment-controlled allow-list for the two required deterministic rules, not a general rule language or rules UI.
- Stage executions remain append-only JSONB events rather than a normalized attempts table.
- Schema setup uses `create_all` plus one idempotent inline `ALTER TABLE`; migration tooling remains final-hardening work.
- A stale same-corpus run is failed with instructions to start a fresh incremental run; automatic proposal rebasing is intentionally not implemented.
- Exact report ordering is append-oriented. The acceptance criterion is structural content/provenance and unchanged unrelated sections, not presentation sorting.
- No hosted provider, React UI, MCP, authentication, workers, or deployment has been added.

### Remaining work after Phase 2

- Phase 3: exactly one configurable hosted provider, schema-constrained model outputs, trusted/untrusted prompt construction, and opt-in real-inference smoke test.
- Phase 4: minimal React interface and polished local demo/setup flow.
- Phase 5: fresh-clone verification, README, formal migrations if retained, limitations, and cleanup.
- Phase 6 remains optional and unauthorized.

## Phase 3 implementation report

### Provider and model

- Provider: OpenAI through the official asynchronous Python SDK and Responses API.
- Default hosted model: `gpt-4o-mini-2024-07-18`, a pinned snapshot supporting Structured Outputs.
- Selection rationale: this is a focused classification/extraction workload, and the model's documented standard pricing is $0.15 per million input tokens and $0.60 per million output tokens.
- The model, base URL, retry count, timeout, and pricing inputs remain environment-configurable.
- `LLM_PROVIDER=fake` remains the default. Hosted inference is activated only with `LLM_PROVIDER=openai`.

### Configuration

Required for hosted inference:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=<key>
```

Optional settings and defaults:

```env
OPENAI_MODEL=gpt-4o-mini-2024-07-18
OPENAI_BASE_URL=
OPENAI_MAX_RETRIES=2
OPENAI_TIMEOUT_SECONDS=60
OPENAI_INPUT_COST_PER_MILLION=0.15
OPENAI_OUTPUT_COST_PER_MILLION=0.60
```

Selecting OpenAI without a key fails at application startup with a specific configuration error. Fake mode never reads or requires the key.

### LLM operations

- The active workflow uses hosted inference for document classification and batched structured claim extraction (one extraction call per document, not one per fact type).
- The provider also implements the existing `compare_claims`, `interpret_rules`, and `propose_updates` interface methods with Structured Outputs.
- The current workflow intentionally continues to use deterministic code for normalized conflict detection, the two checklist rules, affected-scope calculation, provenance validation, and structural report proposals. This is cheaper and more predictable than invoking the hosted model where semantics are unnecessary.

### Structured output and trust boundary

- Every hosted operation calls `AsyncOpenAI.responses.parse` with an operation-specific Pydantic model configured with `extra="forbid"`.
- Classification is restricted to an enum of supported document kinds.
- Claim types are restricted to the six supported fact types; span indexes and confidence values have numeric constraints.
- Normalized values and proposed values are returned as JSON strings, decoded locally, and required to be JSON objects before conversion to domain types.
- The provider explicitly revalidates `response.output_parsed` with Pydantic even after SDK parsing.
- A missing parsed response/refusal is an error rather than an empty successful extraction.
- System instructions and untrusted document spans are separate messages. Source spans are JSON-encoded under `UNTRUSTED_DOCUMENT_DATA`; source instructions cannot enter the trusted system message.
- Existing application grounding still verifies exact evidence, span index, filename, document ID, and corpus before claims or report changes can commit.

### Retry, fallback, and failure behavior

- The OpenAI SDK receives `OPENAI_MAX_RETRIES=2` for transient transport/API retries and a 60-second default timeout.
- If retries are exhausted, the existing LangGraph failure route persists the completed work, failed stage, error, retry guidance, and a resumable interrupt.
- Resuming skips already completed per-document calls.
- There is deliberately no silent automatic fallback from OpenAI to `FakeLLMProvider`; mixing synthetic fixture output into a real run would be misleading. Fake mode is an explicit provider choice.

### Tests and smoke-test result

- `make test`: **19 passed, 1 skipped in 5.11s**.
- All 16 pre-Phase-3 deterministic tests still pass unchanged.
- Three additional deterministic hosted-provider contract tests verify structured schemas, token/cost accounting, trusted/untrusted message separation, second-layer Pydantic validation, and missing-key behavior.
- `make test-live-llm`: **1 skipped, 19 deselected** because `OPENAI_API_KEY` is not configured in the implementation environment.
- No real API request was made, so there is no measured real-inference token usage, cost, latency, or behavioral comparison to report. This is intentionally recorded as not run rather than presented as a success.
- The contract fixture uses 100 input and 25 output tokens and verifies a calculated cost of $0.00003 at the configured model rates. This validates accounting only; it is not a live-usage measurement.
- Expected smoke-test cost is well below one cent for its single short extraction request, but exact tokens/cost will be printed only by an actual run.

To opt into the paid smoke test, copy the environment template, edit `.env` in an editor, and then run:

```bash
cp .env.example .env
make test-live-llm
```

### Expected behavior differences from FakeLLMProvider

- Hosted inference understands ordinary natural-language documents; the fake provider intentionally recognizes only fingerprinted natural-language fixtures.
- Hosted output can vary semantically, refuse, time out, or fail at the API boundary. Structured schemas, exact grounding, budgets, durable retries, and human review contain those differences.
- Entity-key wording and confidence can differ from fake fixtures even when the factual extraction is correct.
- Hosted calls have real token latency/cost; fake calls are local, deterministic, and free.
- A measured comparison remains pending because no API credential was available for the smoke test.

### Known limitations after Phase 3

- Real-inference behavior is not yet empirically verified in this environment; the opt-in smoke test is ready.
- The hosted provider's semantic comparison/rule/proposal methods are implemented but not used by the current deterministic-first graph.
- No embedding model was added, so pgvector semantic fallback remains unused.
- Pricing is configurable and based on the selected model's documented rates; changing models requires updating pricing configuration.
- Phase 2's compact-schema and pre-commit crash limitations remain unchanged.
- No frontend, MCP, deployment, authentication, or optional infrastructure was added.

## Phase 4 implementation report

### Interface and local setup

- Added a small React/TypeScript/Vite single-page interface with four views: Corpus, Run, Review, and Report.
- Corpus supports creation, PDF/DOCX/TXT upload, document listing, initial analysis, one-click Atlas fixture loading, and the Atlas weekly incremental update.
- Run exposes status, conditional workflow stages, per-stage latency/errors, aggregate calls/tokens/cost, affected scope, and retry/review actions.
- Review exposes each conflict, finding, and proposed change independently, including its structured payload and source evidence; every item must be approved or rejected before approved changes can be committed.
- Report renders every structural Project Status Brief section, recent changes, and factual provenance/evidence.
- The browser remembers only the currently active corpus/run IDs in local storage. No authentication, corpus catalog, router, or additional product surface was introduced.
- Added the one small API seam required by the interface: corpus-scoped document listing. Existing workflow and provider behavior are unchanged.
- Added production frontend and backend Dockerfiles, an nginx SPA/API proxy, and a three-service Compose stack. Compose forces `LLM_PROVIDER=fake`, so the demo never requires or reads an API key.
- Expanded the Makefile with `install`, `frontend`, `app`, `demo`, and `down`; existing backend test and API commands remain available.

### Verified commands and results

- `npm run build` in `frontend/`: passed (`tsc -b` and Vite production build).
- `make test`: **19 passed, 1 skipped in 5.41s**. The skip is the intentionally opt-in live OpenAI inference test; every deterministic test passes.
- `make demo`: passed from a Compose build and clean service recreation; PostgreSQL, backend, and frontend all reached healthy state.
- `curl http://localhost:3000/`: served the built React application.
- `curl http://localhost:3000/health`: returned `{"status":"ok","provider":"fake","model":null}` through the frontend proxy, confirming deterministic no-key mode.
- The pinned OpenAI provider/model configuration remains unchanged: `gpt-4o-mini-2024-07-18`; the paid smoke test remains opt-in and was not run without `OPENAI_API_KEY`.

### Known limitations after Phase 4

- The interface intentionally tracks one active corpus/run in local storage; it does not include corpus/run history browsing or multi-user state.
- Runs execute synchronously in the API process, so the UI shows completed/pending persisted stages after each request rather than streaming stage events.
- Evidence and report values favor transparent structured JSON over presentation polish.
- There is no frontend component/E2E browser test suite. The frontend is type-checked and production-built; backend/API behavior remains covered by deterministic integration tests.
- Startup schema creation still uses `create_all` plus the existing idempotent inline alteration rather than formal migrations.
- Real hosted inference is still unmeasured until an API key with billing is supplied.
- Authentication, MCP, deployment, OCR, workers, and optional features remain out of scope.

### Focused Phase 4 UX correction

- Replaced primary raw-JSON review output with type-specific human-readable cards. Conflict cards compare both values and display document, section, and evidence; finding cards show the affected entity and relevant source values; proposed-update cards show their target brief section/entity and current-versus-proposed brief content where available.
- Added explicit copy separating contradiction evaluation from the independently persisted decision to update the Project Status Brief. Every relevant screen states that uploaded evidence is immutable and is never rewritten.
- Replaced raw report rendering with a human-readable Project Status Brief organized into Project Status, Milestones, Deadlines, Owners, Risks, Decisions, Conflicts, Rule Findings, and Recent Changes. Provenance and raw technical data remain expandable.
- Renamed the user-facing Run view to Analysis Run and defined it as one durable workflow execution against a corpus. Backend entity and API names are unchanged.
- The flagship initial Atlas demo now uploads valid `project_plan.pdf`, `meeting_notes.docx`, and `weekly_status_01.txt` files. Its incremental update is the valid `weekly_status_02.pdf`. Parser verification produced 3, 2, 2, and 1 claims respectively, preserving the original seven initial claims, launch-date conflict, unowned-risk finding, and decision-only incremental update.
- Replaced the ambiguous demo reload with **Start fresh Atlas demo**, which always creates a new corpus and run while leaving all previous source documents, checkpoints, and review decisions durable.
- Backend data semantics are unchanged; the only payload addition is conflict type/entity metadata on newly created conflict review items so the UI can name them clearly.
- Post-correction verification: `make test` — **19 passed, 1 opt-in live test skipped in 5.73s**; `npm run build` — passed TypeScript and Vite production build. No live hosted inference was invoked.

### Evidence / draft / committed-brief workflow correction

- The product flow is now Corpus → Analysis → Extracted Info → Draft Brief → Needs Human Decision → Committed Brief.
- `GET /api/runs/{run_id}/claims` is the Evidence Log source and now marks grounded facts as conflicting/non-conflicting and included/excluded from the initial draft. Every fact retains type, entity, normalized value, confidence, document, section/page, and exact evidence.
- Normal grounded non-conflicting claims create structural draft changes but no initial Review items. They automatically populate `GET /api/runs/{run_id}/draft` while the authoritative corpus report remains empty/unmodified.
- Initial review items are limited to one item per conflict and one per rule finding. Conflict and finding duplicate proposed-change review items were removed.
- Initial runs always pause at the durable human gate, including clean corpora with no exception items. `Commit reviewed brief` commits included draft facts plus only approved conflicts/findings.
- Individual ordinary facts remain controllable outside the exception queue through a persisted initial-draft include/exclude endpoint. Exclusion changes only the derived draft/commit; source evidence remains immutable and inspectable.
- Conflict approval deterministically commits one unresolved-conflict entry containing both values, both sources, provenance, and a human-readable unresolved summary. Rejection omits it from the brief.
- Incremental non-conflicting proposals continue to create independent persisted Review items. The UI compares the current committed value with the proposed value and exposes source evidence. Rejected updates leave the committed report structurally unchanged.
- The committed-report API remains authoritative and unchanged in meaning; the draft is an explicit run-scoped projection, not a report version or silent commit.
- Added/updated deterministic tests proving grounded facts appear in claims and draft, initial report remains uncommitted, ordinary facts do not flood Review, conflicts/findings each produce one decision, initial fact exclusion works, incremental changes remain item-level, and rejection preserves the committed brief.
- Final verification: `make test` — **20 passed, 1 opt-in live test skipped in 48.96s**; `npm run build` — TypeScript and Vite production build passed. Hosted inference was not invoked.

### Exact Phase 5 remaining work

- Verify the documented setup and deterministic Atlas/Beacon flows from a fresh clone.
- Complete the reviewer-facing README with prerequisites, architecture summary, API/UI walkthrough, configuration, test/smoke commands, and limitations.
- Decide whether to retain startup schema creation or add the smallest formal migration path needed for submission.
- Run the opt-in real-inference smoke test once an API key with billing is available and record measured tokens, cost, latency, and behavior differences.
- Perform final cleanup and acceptance-check audit. Beacon itself is already implemented and tested from Phase 2; it does not need rebuilding.
- Stop before Phase 6; MCP remains optional and requires separate approval.

## Phase 5 final hardening report

### Fresh-environment verification

- Validated Compose configuration, stopped the normal stack without deleting its persisted volume, and built backend/frontend images with `--no-cache` under a separate `pea_phase5_fresh` Compose project.
- Started PostgreSQL/pgvector, FastAPI, and nginx/React against a newly created database volume. All three services reached healthy state with `LLM_PROVIDER=fake` and no API key.
- Exercised the complete Atlas flow through REST on the clean stack: valid PDF/DOCX/TXT upload; seven grounded claims; uncommitted draft; exactly one conflict and one finding review item; approved initial commit; incremental PDF ingestion; exact `decision:qa_exit` affected scope; one proposed update; rejected update leaving the committed report structurally identical.
- Verified the built frontend was served through nginx. Then stopped the temporary stack and restored `make demo` against the normal persisted volume; all normal services returned healthy.
- The temporary fresh database was isolated by Compose project/volume and never reused existing application state.

### Documentation and cleanup

- Added the root `README.md` with prerequisites, deterministic quick start, Atlas walkthrough, test/development commands, hosted-provider configuration, architecture, API, persistence/incrementality behavior, rules/limits, limitations, scope boundary, and Phase 6 status.
- Added root `.env.example`; Compose now accepts documented provider/guard variables while `make demo` and `make test` explicitly force deterministic fake mode.
- Kept the hosted provider/model unchanged: OpenAI with `gpt-4o-mini-2024-07-18`.
- Pinned frontend dependency versions already proven by the lockfile and refreshed `package-lock.json`.
- Removed generated Vite config outputs from the source surface and ignored future TypeScript-generated artifacts.
- Retained idempotent startup schema setup rather than adding Alembic solely for the take-home. There is no deployed upgrade history; this limitation and decision are documented.

### Acceptance-criteria audit

- **Scope/stack:** Python, FastAPI, LangGraph, PostgreSQL, pgvector, React, PDF/DOCX/TXT, six fact types, one hosted provider abstraction, and deterministic fake provider are present.
- **Workflow:** real conditional routing covers provider/budget failure, grounding retry/escalation, initial/incremental paths, pending-review re-interrupt, and commit/finalize behavior.
- **Grounding/security:** factual commits mechanically revalidate immutable document/span provenance; invalid claims cannot commit; untrusted document instructions are isolated and tested.
- **Conflicts/rules:** Atlas preserves both contradictory launch claims and sources; Beacon proves zero-conflict behavior and non-hardcoding; both deterministic checklist rules are configurable.
- **Human control:** initial conflicts/findings and later proposed updates are independently persisted decisions. Normal initial facts remain individually inspectable/excludable and the draft remains uncommitted until the human gate.
- **Durability/idempotency:** PostgreSQL checkpoints resume the same run ID after application reconstruction; completed-work markers prevent repeated extraction; SHA-256 suppresses duplicate processing.
- **Incrementality/isolation:** only new documents and affected entities are processed; unrelated report sections remain unchanged; concurrent corpora do not leak; stale same-corpus writes fail safely.
- **Report/API/UI:** REST drives the complete flow; the report is structural and provenance-bearing; the UI exposes corpus, stages/metrics, extracted information, draft, review, and committed brief.
- **Observability/errors:** every stage records status, latency, calls, tokens, cost, and error; configurable guards preserve work and provide retry guidance.
- **Demo/setup:** Atlas uses valid PDF/DOCX/TXT plus an incremental PDF; Beacon is distinct; `make demo` is no-key; `make test` requires no hosted service.
- **Tests:** every behavior in the Task 1 required-test matrix is covered by deterministic tests.

### Final verification

- `make test`: **20 passed, 1 skipped in 47.13s**. The skip is exclusively the opt-in live OpenAI smoke test.
- `npm run build`: TypeScript and Vite production build passed.
- No hosted request was made and no token/cost claim is fabricated.

### Mandatory item still pending external configuration

- The hosted integration and contract tests are complete, but the Task 1 real-inference smoke run has not been executed because no billed `OPENAI_API_KEY` is available. Before submission, run `make test-live-llm` with billing enabled and record actual tokens, cost, latency, and behavior. No other mandatory implementation item was found missing.

### Phase 6 boundary

- MCP was subsequently authorized and is implemented by the Phase 6 report below.

## Phase 6 MCP integration report

### Thin shared-application design

- Added the official Python MCP SDK v2 and one `project_evidence.mcp_server` adapter.
- Each MCP tool makes an in-process ASGI request to the existing FastAPI route. FastAPI, LangGraph, PostgreSQL rows/checkpoints, provenance validation, review gates, providers, incrementality, metrics, and concurrency rules remain the only implementation of application behavior.
- MCP owns no workflow state, domain tables, report mutations, or provider logic. The only additive REST seam is corpus listing so agents can select an existing corpus.
- Both stdio and local Streamable HTTP transports are supported. HTTP defaults to `127.0.0.1:8001/mcp` and is intended for local development, not deployment.

### Tools

- `create_corpus`, `list_corpora`, `list_documents`, `add_document`
- `start_analysis`, `get_analysis`, `resume_analysis`
- `get_extracted_information`, `set_draft_fact_inclusion`, `get_draft_brief`
- `get_review_items`, `submit_review_decisions`, `get_committed_brief`

Document bytes are base64 at the MCP boundary and then pass through the existing multipart upload route, preserving format checks, immutability, and SHA-256 reuse. Review submission only persists decisions; resumption still passes through the existing LangGraph interrupt/commit gate.

### MCP verification

- The official SDK's in-memory MCP `Client` negotiates the protocol, lists all 13 tools, and executes the complete workflow test.
- Through MCP only, the test creates Atlas and Beacon corpora, uploads evidence, runs initial analysis, reads grounded claims/draft/review, commits both briefs, verifies disjoint provenance, adds an Atlas incremental document, rejects its one targeted change, and proves the committed Atlas brief is unchanged.
- A live official MCP client also connected over Streamable HTTP at `/mcp` and discovered all 13 tools.
- Final `make test`: **21 passed, 1 opt-in live inference test skipped in 7.34s**.

## Scope boundary

Build a project-delivery document analysis system for PDF, DOCX, and TXT documents. It extracts only `milestone`, `deadline`, `owner`, `risk`, `decision`, and `status` facts; validates their provenance; detects conflicts; evaluates a small configured checklist; proposes structural report changes; and commits only individually approved changes.

The design deliberately excludes authentication, user accounts, OCR, queues/workers, multiple hosted providers, arbitrary-domain intelligence, agent chat, elaborate dashboards, cloud deployment, and MCP.

## Minimum architecture

Use a single repository and a modular monolith:

1. **FastAPI API** — thin request/response adapters for the required REST operations.
2. **Application services** — corpus/document ingestion, run orchestration, review, and report commits. Transactions and authorization-independent business rules live here.
3. **LangGraph workflow** — one state graph with real conditional routing for validation retry/escalation, budget/error exits, initial versus incremental analysis, and human-review pause/resume.
4. **Deterministic domain logic** — provenance validation, conflict candidate selection/comparison where values are normalized, rule evaluation where possible, affected-scope calculation, approval enforcement, and budget guards.
5. **LLM provider port** — one injectable async interface, implemented by a deterministic fake first and one configurable hosted provider later.
6. **Parsers** — small adapters for PDF, DOCX, and TXT that emit page/section-aware source spans. No OCR.
7. **PostgreSQL + pgvector** — the sole durable store. Normal relational tables are authoritative; vectors are used only to retrieve semantically relevant prior claims for targeted incremental work.

No Redis, Celery, Kafka, microservices, event bus, or separate vector database is needed. Runs can execute in the API process for this take-home; persisted stage state makes that safe to resume after process interruption.

### Ownership boundary

- The LLM may classify documents, extract candidate claims, compare semantically ambiguous claims, interpret genuinely semantic rules, and draft structured update proposals.
- Application code owns IDs, corpus/run scoping, hashes, source spans, grounding checks, normalization validation, retries, budgets, stage transitions, persistence, conflict/rule records, review decisions, and commits.
- A LangGraph checkpoint is a resumable control-flow snapshot, not the system of record. Domain rows remain authoritative and independently inspectable.

## Proposed repository structure

```text
.
├── PROGRESS.md
├── README.md                         # later phase
├── Makefile                          # later phase
├── .env.example                      # hosted provider config; later phase
├── docker-compose.yml                # later phase
├── backend/
│   ├── pyproject.toml
│   ├── migrations/
│   ├── src/project_evidence/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── api/
│   │   │   ├── corpora.py
│   │   │   ├── runs.py
│   │   │   ├── reviews.py
│   │   │   └── reports.py
│   │   ├── domain/
│   │   │   ├── models.py
│   │   │   ├── enums.py
│   │   │   ├── grounding.py
│   │   │   ├── conflicts.py
│   │   │   ├── rules.py
│   │   │   └── budgets.py
│   │   ├── services/
│   │   │   ├── ingestion.py
│   │   │   ├── runs.py
│   │   │   ├── reviews.py
│   │   │   └── reports.py
│   │   ├── workflow/
│   │   │   ├── graph.py
│   │   │   ├── state.py
│   │   │   └── nodes.py
│   │   ├── llm/
│   │   │   ├── base.py
│   │   │   ├── fake.py
│   │   │   └── hosted.py             # Phase 3
│   │   ├── parsing/
│   │   │   ├── base.py
│   │   │   ├── pdf.py
│   │   │   ├── docx.py
│   │   │   └── text.py
│   │   └── persistence/
│   │       ├── db.py
│   │       ├── repositories.py
│   │       └── checkpoints.py
│   └── tests/
│       ├── fixtures/
│       ├── unit/
│       └── integration/
├── demo_data/                         # synthetic Atlas, then Beacon
│   ├── atlas/
│   └── beacon/
└── frontend/                          # Phase 4; not created now
```

Files should remain grouped by responsibility, but modules should not be split further until their size justifies it.

## Database entities and relationships

All mutable/queryable workflow data includes `corpus_id`; run-produced data also includes `run_id`. Foreign keys and repository methods enforce this isolation.

| Entity | Essential fields | Relationships / constraints |
|---|---|---|
| `corpus` | `id`, `name`, timestamps | Owns documents, runs, report sections |
| `document` | `id`, `corpus_id`, filename, media type, SHA-256, storage reference/content, parse status | Unique `(corpus_id, sha256)` makes upload idempotent |
| `source_span` | `id`, `document_id`, page, section, text, offsets, optional embedding | Parsed evidence addressable by claims; page may be null for TXT but a stable span is mandatory |
| `run` | `id`, `corpus_id`, kind (`initial`/`incremental`), status, base report version, budgets, error/retry metadata, timestamps | Owns run documents, stages, candidates, review items; run ID is checked against corpus on every operation |
| `run_document` | `run_id`, `document_id`, role (`new`/`existing_context`) | Unique pair; incremental runs mark only newly processed documents as `new` |
| `stage_execution` | `id`, `run_id`, stage, attempt, status, latency, calls/tokens/cost, error, timestamps | Unique `(run_id, stage, attempt)`; supports observability and completed-stage reuse |
| `claim` | `id`, `corpus_id`, `run_id`, `document_id`, `source_span_id`, type, subject/entity key, text, normalized value, confidence, grounding status, extraction version | A grounded claim must reference an existing span belonging to its document/corpus; duplicate extraction is prevented by a stable fingerprint |
| `conflict` | `id`, `corpus_id`, `run_id`, fact type, entity key, status | Joins 2+ claims through `conflict_claim`; a run may correctly create none |
| `conflict_claim` | `conflict_id`, `claim_id` | Preserves every conflicting value and source |
| `rule_definition` | `id`, description, evaluator kind/config, enabled | Small configurable checklist; deterministic evaluator selected when available |
| `rule_finding` | `id`, `corpus_id`, `run_id`, `rule_id`, entity key, message, status | Links supporting claims/spans via a small join table; zero findings is valid |
| `proposed_change` | `id`, `corpus_id`, `run_id`, section key, operation, target item ID, structured before/after values, provenance links, status | Surgical operation (`add`/`replace`/`remove`); never an opaque whole-report rewrite |
| `review_item` | `id`, `corpus_id`, `run_id`, item type, referenced item ID, status, decision note, decided_at | One row per conflict, finding, or proposed change; unique target; decisions persisted independently |
| `report` | `id`, `corpus_id`, current version | One logical report per corpus |
| `report_section` | `report_id`, section key, structured JSON value, version, updated_by_change_id | Sections: status, milestones, deadlines, owners, risks, decisions, conflicts, findings, recent changes |
| `report_item_provenance` | report section/item identity, `claim_id` | Ensures every factual report item traces to grounded claims |
| `report_revision` | `id`, `report_id`, version, `run_id`, committed_at | Audit boundary for an approved batch; proposed changes record exact item changes |
| LangGraph checkpoint tables | thread/checkpoint IDs and serialized graph state | PostgreSQL checkpointer; thread ID equals run ID |

`document` can store bytes in PostgreSQL for the smallest self-contained implementation, or a storage path if Docker volume storage is chosen later. The invariant is durable content plus stable source spans; object storage is unnecessary.

## LangGraph design

### State

Keep graph state compact and serializable. Store large artifacts in domain tables and carry IDs/scope summaries in the graph.

```python
class WorkflowState(TypedDict):
    corpus_id: UUID
    run_id: UUID
    run_kind: Literal["initial", "incremental"]
    new_document_ids: list[UUID]
    pending_document_ids: list[UUID]
    affected_fact_types: list[FactType]
    affected_entity_keys: list[str]
    claim_ids: list[UUID]
    validation_attempt: int
    invalid_claim_ids: list[UUID]
    conflict_ids: list[UUID]
    finding_ids: list[UUID]
    proposed_change_ids: list[UUID]
    pending_review_item_ids: list[UUID]
    llm_calls: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal
    last_completed_stage: str | None
    status: RunStatus
    error: ErrorInfo | None
```

Counters are mirrored into `stage_execution`; reducers add node deltas safely. Claim/report content is referenced by ID rather than duplicated in checkpoints.

### Nodes

1. `prepare_run` — validates `(corpus_id, run_id)`, selects unprocessed/new documents, and exits idempotently when there is no work.
2. `ingest_parse` — parses only pending documents into durable source spans.
3. `classify` — one batched provider call per new document where needed.
4. `extract_claims` — batched structured extraction of all supported fact types for each new document; persists candidates and usage.
5. `validate_grounding` — deterministic source/document/span/evidence checks and normalized-value schema checks.
6. `prepare_retry` — retains valid claims and prepares only invalid/repairable claims for a bounded retry.
7. `escalate_invalid` — persists an actionable review/error item when retries are exhausted; unsupported claims remain uncommittable.
8. `compute_affected_scope` — derives fact types/entity keys from new valid claims.
9. `retrieve_relevant_claims` — loads new claims plus matching prior claims by corpus, fact type/entity key, with pgvector as bounded semantic fallback.
10. `detect_conflicts` — deterministic normalized comparisons first; provider comparison only for ambiguous semantic equivalence.
11. `evaluate_rules` — runs only rules whose dependencies intersect affected scope; deterministic rules first.
12. `propose_changes` — produces item-level structural operations only for affected report sections/entities.
13. `create_review_items` — persists separate items for conflicts, findings, and proposed changes.
14. `await_human_review` — LangGraph interrupt; no commit occurs here.
15. `commit_approved` — transactionally applies approved proposed changes only, verifies provenance again, records a revision/recent changes, and leaves rejected/pending items untouched.
16. `finalize_run` — marks status complete and finalizes metrics.
17. `fail_gracefully` — persists stage/error details and whether retry/resume is possible without deleting completed work.

Each meaningful node writes its domain output and `stage_execution` row in a transaction before the checkpoint advances. Nodes use stable keys/upserts so replay after a crash is idempotent.

### Edges and conditional transitions

```text
START -> prepare_run
prepare_run -- no new work --> finalize_run
prepare_run -- work --> ingest_parse -> classify -> extract_claims
extract_claims -> validate_grounding
validate_grounding -- invalid + retry allowed/budget available --> prepare_retry -> extract_claims
validate_grounding -- invalid + retries exhausted --> escalate_invalid
validate_grounding -- valid/no repairable invalid --> compute_affected_scope
escalate_invalid -> compute_affected_scope
compute_affected_scope -> retrieve_relevant_claims -> detect_conflicts
detect_conflicts -> evaluate_rules -> propose_changes -> create_review_items
create_review_items -- review items exist --> await_human_review (interrupt)
create_review_items -- none --> finalize_run
await_human_review -- pending decisions remain --> await_human_review (interrupt again)
await_human_review -- all required decisions recorded --> commit_approved
commit_approved -> finalize_run -> END
any LLM node -- call/cost guard exceeded --> fail_gracefully -> END
any node -- recoverable provider/system error --> fail_gracefully -> END (resumable)
```

An incremental run uses the same graph; `prepare_run`, `compute_affected_scope`, retrieval, rules, and proposal generation change their query scope based on `run_kind`. This avoids a second workflow that could drift.

## LLMProvider interface

The interface returns validated structured result objects plus normalized usage metadata. It does not persist data or choose workflow transitions.

```python
class LLMProvider(Protocol):
    async def classify_document(
        self, request: ClassifyDocumentRequest
    ) -> LLMResult[DocumentClassification]: ...

    async def extract_claims(
        self, request: ExtractClaimsRequest
    ) -> LLMResult[list[ExtractedClaim]]: ...

    async def compare_claims(
        self, request: CompareClaimsRequest
    ) -> LLMResult[ClaimComparison]: ...

    async def interpret_rules(
        self, request: InterpretRulesRequest
    ) -> LLMResult[list[InterpretedFinding]]: ...

    async def propose_updates(
        self, request: ProposeUpdatesRequest
    ) -> LLMResult[list[ProposedReportOperation]]: ...
```

`LLMResult[T]` contains `value`, call count, input/output tokens, estimated cost, provider/model identifiers, and latency. Request types explicitly separate trusted instructions/schema from `untrusted_document_content`; providers delimit/encode source content and require schema-constrained outputs. Returned provenance is still verified against stored source spans.

`FakeLLMProvider` maps fixture IDs/content fingerprints to deterministic typed outputs, records every call, can inject configured failures, and never uses a key or network. `HostedLLMProvider` is deferred to Phase 3 and selects model, credentials, base URL, and pricing config from environment variables.

## Checkpoint and resume

- Compile the graph with LangGraph's PostgreSQL checkpointer and `thread_id = run_id`.
- Persist a checkpoint after every meaningful node. Domain output is written first with idempotent keys; checkpoint advancement follows.
- On `POST /api/runs/{id}/resume`, load the run, verify its corpus scope and resumable status, then invoke the graph with the same thread ID. LangGraph continues at the next node or suspended review interrupt.
- If a crash happens between domain commit and checkpoint save, replayed node upserts find the existing output instead of calling the provider again. A completed `stage_execution` plus matching input fingerprint is the durable reuse guard.
- Provider failures mark the stage/run failed-resumable with an actionable error. Prior claims, spans, and stage metrics remain intact.
- Review decisions are domain records, not only checkpoint state. Resume queries them before `commit_approved`.

Automated proof: run through extraction with a fake provider call counter, simulate interruption before the next checkpoint/stage, reconstruct the app/graph and checkpointer, resume the same run ID, and assert extraction rows/call counts did not increase.

## Incremental update strategy

1. Hash upload bytes. A duplicate `(corpus_id, sha256)` returns the existing document and schedules no model work.
2. An incremental run parses/classifies/extracts only new `run_document` rows.
3. Grounded new claims yield an affected set of `(fact_type, entity_key)` values. Entity keys are deterministic normalized subjects (for example, milestone identity or risk identity), with semantic candidate retrieval only when exact normalization is insufficient.
4. Retrieve existing grounded claims only for that set, scoped to the same corpus. Use vector similarity as candidate generation, never as authority.
5. Re-run conflict detection for affected entities and only rules declaring dependencies on affected types/entities.
6. Generate `proposed_change` operations addressed to exact report sections/items. No whole-report generation is allowed.
7. Human review remains item-level. `commit_approved` locks the current report row, checks the base version, applies only approved operations, and increments the report version.
8. Unaffected `report_section` rows are neither rewritten nor version-changed. A concurrent stale proposal is rejected for rebase/re-evaluation rather than silently overwriting newer data.

Tests snapshot every report section and provider call log before adding `weekly_status_02`; afterward they assert only the new document was extracted, retrieval/rules were bounded to the affected scope, expected targeted changes appeared, and byte-for-byte/structural values plus versions of unrelated sections stayed unchanged.

## Testing strategy

`make test` will select `FakeLLMProvider` and require no API key or network. Tests should use PostgreSQL with pgvector because checkpointing, constraints, transactions, concurrency, and vector queries are core behavior; Docker Compose can supply an isolated test database. Most domain tests remain fast unit tests, while workflow/API tests exercise the real database and checkpointer.

### Fake provider behavior

- Fixture-driven typed responses for Atlas and Beacon, keyed by document content hash or explicit fixture marker—not project names embedded in business logic.
- Call ledger with method, run/corpus context, and input fingerprint for idempotency/incremental assertions.
- Configurable malformed provenance, provider exception, latency gate, and usage/cost values.
- Documents containing injection text are treated as source text; fixture output reflects actual project facts, and tests also verify no document instruction is interpolated into trusted prompt/system fields.

### Required coverage mapping

- Basic workflow/provenance: `test_basic_run`, `test_grounded_claim_has_source`, `test_unsupported_claim_not_committed`.
- Conflict correctness: `test_conflict_detected`, `test_clean_corpus_can_have_zero_conflicts`.
- Deterministic rules: `test_rule_finding`, plus a passing-rule zero-finding assertion.
- Review/commit boundary: `test_item_level_review`, `test_rejected_item_not_committed`, `test_approved_item_committed`.
- Durability/idempotent replay: `test_resume_after_interruption`, `test_completed_stage_not_repeated`, including app reconstruction.
- Upload idempotency: `test_duplicate_document_idempotency`, asserting no extra provider calls.
- Trust boundary: `test_prompt_injection_ignored`, asserting workflow/report state cannot be controlled by source instructions.
- Isolation: `test_concurrent_runs_are_isolated`, started concurrently against different corpora and checked at all tables/report endpoints.
- Incrementality: `test_incremental_update`, `test_incremental_update_preserves_unaffected_sections`, with call-log and section-version assertions.
- Generality: `test_second_corpus`, using distinct Beacon fixtures.
- Cost controls: `test_budget_limit_fails_gracefully`, asserting durable prior work and clear resumability/error metadata.
- Additional focused tests: parser source spans, provenance corpus mismatch rejection, run/corpus mismatch rejection, pending-review commit rejection, report version race, and zero-new-work resume.

Hosted inference gets a separately marked smoke test in Phase 3 and is excluded from `make test` by default.

## Architecture decisions

1. **Modular monolith:** smallest deployable/runtime shape that still keeps routes, workflow, domain policy, providers, parsing, and persistence separable.
2. **Relational domain state plus LangGraph checkpoints:** avoids opaque checkpoint-only business data and enables audit/review/report queries.
3. **One graph for initial and incremental runs:** shared correctness, conditional scope.
4. **Structural report sections and item operations:** necessary for targeted updates and unchanged-section guarantees.
5. **Deterministic-first decisions:** exact normalization, grounding, known checklist rules, budgets, and commits stay in code; LLM calls are reserved for semantic tasks.
6. **PostgreSQL-backed integration tests:** SQLite cannot prove required checkpoint, pgvector, transaction, and concurrency behavior.
7. **In-process execution initially:** sufficient for a take-home because durable checkpoints provide recovery; a worker system is explicitly unnecessary.
8. **Item-level review records:** conflicts, findings, and changes are independently decidable; only approved proposed changes mutate the report.
9. **Source spans as first-class records:** makes provenance mechanically verifiable rather than prompt-enforced.
10. **Stable fingerprints and idempotent upserts:** protect both duplicate uploads and crash-window replays from repeated expensive calls.

## Assumptions and requirement tensions

1. **The requested sequencing versus the specification's phases:** the specification places checkpointing, incremental behavior, prompt-injection defense, concurrency, observability, and many mandatory tests in Phase 2, while the request says not to implement Phase 2 yet. This document designs them so the Phase 1 schema has the right seams, but the next implementation step will stop at Phase 1 unless approval explicitly expands it.
2. **Docker Compose timing:** easy setup makes Docker Compose preferred/ultimately required for reviewer convenience, but the request explicitly defers deployment and frontend. Compose/Makefile are therefore Phase 4 artifacts, not part of the next Phase 1 implementation. During development, tests may still need an already available PostgreSQL instance until Compose is authorized.
3. **Demo mode wording:** the required stack asks for a deterministic fake for tests/demo mode, while the implementation order places the full demo setup later. Phase 1 will implement the fake provider and backend fixtures as needed; polished `make demo` arrives in Phase 4.
4. **Human review of findings/conflicts versus commit semantics:** the task says all three item types are independently approved/rejected, but only says approved proposed changes may be committed. Assumption: decisions on conflicts/findings are persisted and can inform separately generated report operations; approving a conflict/finding alone does not directly mutate the report.
5. **Retries with partially invalid extraction:** valid grounded claims are retained; only invalid/repairable outputs are retried. After exhaustion, invalid claims are escalated and remain ineligible for report commits, while valid claims may continue through the workflow.
6. **TXT provenance:** TXT has no native page number. Use a stable source span with section/line offsets and `page = null`; provenance remains valid because evidence is verified against that stored span.
7. **Document replacement:** same hash in one corpus is a duplicate; changed content is a new immutable document version. No destructive replacement behavior is required.
8. **Concurrency:** required isolation is satisfied through scoped keys/queries and transactions. Correct simultaneous edits to the same corpus are additionally protected by report version checking, though the explicit test covers independent corpora.
9. **Hosted provider choice:** the task requires one hosted provider but does not specify which. Select one during Phase 3, configurable via environment; business logic must remain provider-neutral.
10. **Embeddings in no-key mode:** pgvector is required, but tests cannot require an embedding API. Fake/deterministic local vectors or exact entity matching will be used in tests; vectors are only candidate retrieval aids.
11. **Rules configuration format:** YAML examples are illustrative. Store normalized definitions in PostgreSQL and optionally seed them from a small YAML file; no general rule language or rules UI is required.
12. **Recent changes:** retain it as a structural report section populated deterministically from committed report revisions, not LLM prose.

No requirements appear logically impossible under these assumptions. The main tension is implementation order, resolved by designing for later reliability now without implementing that phase before approval.

## Implementation phases

### Phase 0 — Design (complete)

- Read the complete specification.
- Define the minimum architecture, data model, graph, provider port, recovery/incremental behavior, tests, assumptions, and acceptance checklist.
- Await approval.

### Phase 1 — Backend vertical slice (complete)

- [x] Create Python/FastAPI project and compact PostgreSQL schema with corpus/run scoping.
- [x] Implement parsers, source spans, deterministic fake provider, grounding, conflict/rule logic, graph conditional routing, item review, and structural report commit.
- [x] Make Atlas complete through backend APIs.
- [x] Add basic PostgreSQL checkpoint/restart resume and focused tests.
- [x] Stop without claiming Phase 2 reliability acceptance.

### Phase 2 — Reliability (complete)

- [x] PostgreSQL checkpoint/resume and durable completed-stage replay protection.
- [x] Upload and processed-document idempotency.
- [x] Incremental affected scope and unchanged-section preservation.
- [x] Prompt-injection trust boundary coverage.
- [x] Concurrent corpus isolation and stale same-corpus write rejection.
- [x] Stage metrics, provider recovery, and call/cost guards.
- [x] Clean second corpus and required Phase 2 reliability tests.

### Phase 3 — Real inference (complete)

- [x] Add exactly one configurable OpenAI hosted provider.
- [x] Use operation-specific Pydantic Structured Outputs.
- [x] Preserve trusted/untrusted prompt separation and application grounding.
- [x] Keep `FakeLLMProvider` as the no-key default.
- [x] Add deterministic provider contract tests and an opt-in paid smoke test.
- [ ] Execute the paid smoke test when an API key is supplied.

### Phase 4 — Interface/setup (complete)

- [x] Minimal React UI for corpus, run stages/metrics, item review/evidence, and structural report.
- [x] Docker Compose and Makefile commands for the complete local application.
- [x] Deterministic no-key Atlas `make demo` flow using `FakeLLMProvider`.

### Phase 5 — Final hardening (complete)

- [x] Verify fresh Compose images and a new PostgreSQL volume.
- [x] Exercise Atlas initial and incremental flows in deterministic no-key mode.
- [x] Document setup, architecture, workflow, API, configuration, limitations, and reviewer walkthrough.
- [x] Audit Task 1 acceptance criteria and clean generated/configuration artifacts.
- [ ] Execute the paid real-inference smoke test when a billed API key is supplied.

### Phase 6 — MCP integration (complete)

- [x] Thin MCP adapter over the existing FastAPI/application workflow.
- [x] Core corpus, document, run, evidence, draft, review, commit, and incremental tools.
- [x] Stdio and local Streamable HTTP launch paths.
- [x] End-to-end protocol and cross-corpus isolation test.
- [x] README tool catalog, connection instructions, example agent flow, and transport relationship.

## Acceptance checklist

### Scope and setup

- [x] Supports PDF, DOCX, TXT without OCR.
- [x] Supports only the six required fact types.
- [x] `make demo` starts a deterministic no-key demo.
- [x] `make test` runs without an API key or external LLM.
- [x] PostgreSQL/pgvector setup is automatic through Docker Compose.

### Workflow and grounding

- [x] LangGraph has exercised conditional validation retry/escalation, review interrupt/resume, and graceful budget/error routes.
- [x] Every factual claim/report item has mechanically verified source provenance.
- [x] Unsupported/ungrounded claims cannot be committed.
- [x] Uploaded source instructions cannot control prompts, graph routing, state, or reports.

### Conflicts, rules, and review

- [x] A deliberate conflict preserves all claims/sources; a clean corpus produces zero conflicts.
- [x] Deterministic rules produce true findings and no invented passing findings.
- [x] Conflicts, findings, and incremental proposed changes receive independent persisted decisions; normal initial draft facts remain individually excludable.
- [x] Only human-included initial draft facts and approved exceptions/updates mutate the report; rejected/pending changes do not.

### Persistence, incremental work, and isolation

- [x] Restarting the same run resumes from its durable checkpoint.
- [x] Completed expensive stages are not repeated after interruption/replay.
- [x] Duplicate upload by corpus/hash causes no duplicate processing/model call.
- [x] Incremental runs process only new documents and relevant existing claims/rules.
- [x] Unaffected report section values remain unchanged.
- [x] Concurrent corpora/runs never mix claims, findings, reviews, metrics, or reports.

### API, report, observability, and demo data

- [x] Required conceptual REST operations drive the complete workflow with thin routes.
- [x] The report is structural and contains status, milestones, deadlines, owners, risks, decisions, conflicts, findings, and recent changes.
- [x] Stage status, latency, call count, token usage, cost, and errors are visible.
- [x] Call/retry/cost guards fail clearly and preserve completed work.
- [x] Atlas demonstrates conflict, rule finding, normal facts, and incremental update.
- [x] Beacon is genuinely distinct and passes without hardcoded Atlas logic.
- [x] Minimal UI supports corpus, run, item review, evidence, report, and recent changes.
- [x] Every required backend behavior named in the `TASK.md` test matrix is covered and passes without a live LLM.
- [x] MCP exposes the same core workflow without duplicating domain or graph logic, with protocol-level isolation coverage.

## Next gate

Phase 6 MCP integration, the no-key hardening pass, and a measured hosted Atlas run are complete. A single follow-up paid Atlas run is recommended only to verify the confirmed normalization corrections; deployment and unrelated optional work remain out of scope.

## Final hardening pass (complete)

- [x] Corpus-scoped JSON rulesets exposed through REST, MCP, and the existing Corpus UI; supported deterministic operators remain deliberately small.
- [x] Claim embeddings persisted in pgvector with exact-first, vector-candidate, deterministic-authority entity matching.
- [x] Atlas and Beacon fixtures rewritten as natural project prose; valid Atlas PDF/DOCX/TXT assets regenerated.
- [x] `FakeLLMProvider` changed from record parsing to natural-sentence fingerprint lookup.
- [x] Document classification persisted and passed into extraction context; exposed with document metadata.
- [x] `recent_changes` records now carry timestamp, run, section/entity, old/new structural values, and source provenance.
- [x] Optional REST and default MCP/UI document-arrival trigger creates a focused incremental run only after a committed brief exists.
- [x] Initial normal facts have persisted pending/included/excluded states; unresolved state blocks commit and excluded facts remain evidence but do not enter the brief.
- [x] Secret guidance uses `.env` editing only; `.env` remains ignored and provider error persistence redacts the configured key.
- [x] Final complete deterministic backend result: **27 passed, 1 opt-in paid test skipped in 13.10s**.
- [x] Final frontend TypeScript and Vite production build passed (16 modules; 217.08 kB JS / 67.27 kB gzip).
- [x] Rebuilt both Compose images; a separately named project booted healthy on a fresh PostgreSQL volume and passed the complete 27-test Atlas/Beacon/MCP suite before the original persisted stack was restored.
- [x] Hosted OpenAI inference was exercised by a full Atlas run: 7 calls, 3,073 input tokens, 782 output tokens, and $0.00093015 estimated cost.

## Hosted Atlas diagnostic correction

- Persisted run `358eefd6-f465-4311-b087-1d0eb2a4a405` recorded 7 calls: 3 classifications, 3 initial extractions, and 1 targeted extraction retry. Runtime identity was OpenAI / `gpt-4o-mini-2024-07-18`; historical run rows did not yet snapshot provider/model, so new runs now persist both fields in metrics.
- Generic entity-role tokens no longer establish semantic identity; project health status and deployment status remain distinct while production launch/rollout aliases still match.
- Missing owner representations normalize to JSON null before fingerprinting, storage, and conflict comparison.
- Negative/meta statements that explicitly deny being a project decision are omitted in the hosted adapter and at the shared workflow ingestion boundary.
- Provider/model labels in the React UI now come from runtime/run metadata instead of a hard-coded deterministic banner.
- Final deterministic verification after this correction: **30 passed, 1 paid test skipped**; frontend TypeScript/Vite production build passed.

## Post-live combined-analysis and report correctness pass

- Hosted classification and extraction are now one strict structured analysis call per newly processed document. The durable completion marker and metrics stage are named `analyze`; retries remain isolated per document. Eight initial Atlas documents therefore mean eight normal hosted calls, not sixteen, and the one-document incremental update means one call.
- Structural report sections, including `status`, are lists of independently keyed items. Upsert and audit comparison use canonical `item_key`, so overall project status and deployment status coexist and a new entity records `old_value: null`.
- Risk structured output retains supported `severity`, `owner`, and `status`; deterministic rules consume those fields. Incremental rule evaluation uses newly arrived evidence so superseded older risk values do not create a stale new finding.
- Conflict review persists one of four human outcomes: keep unresolved, select any supplied claim, or exclude. A selected value enters the applicable brief section while the conflict audit record retains every claim and provenance object. REST and the thin MCP adapter expose identical fields.
- Draft and committed brief views hide empty sections, group milestone/date/owner facts into readable delivery concepts, provide one Sources/evidence expansion per logical item, and keep raw structures nested as secondary technical detail. Recent Changes is a collapsed compact audit trail.
- Atlas now contains eight realistic initial documents (`project_charter.pdf`, `project_plan.pdf`, `release_plan.docx`, two DOCX meeting notes, TXT weekly status, `risk_register.pdf`, and `change_request_01.pdf`) plus focused `change_request_02.docx` incremental evidence. Most facts agree; one launch contradiction and one unowned-high-risk finding are intentional. Beacon remains distinct.
- Conservative hosted-cost estimate for the eight-document Atlas analysis is below **$0.01** at the pinned model rates and comfortably below the `$0.25` run guard. No paid call was made during this pass.
- Final deterministic verification: **35 passed, 1 opt-in paid test skipped in 27.11s**. This includes Atlas initial/incremental, Beacon isolation/clean flow, all conflict outcomes, checkpoint/replay, and MCP end to end. Frontend TypeScript and Vite production build passed (16 modules; 219.58 kB JS / 67.89 kB gzip).

## Atlas evidence-density rewrite

- Defined the controlled oracle first in `atlas_ground_truth.py`: per-document classifications and claims, intended non-facts/decoys, expected initial brief state, single conflict/finding, clean non-findings, and incremental changed/unchanged scope.
- Rewrote every Atlas fixture as natural workplace material with narrative context, headings, operating detail, stale history, tentative proposals, explicit decision records, supporting repetition, and irrelevant content. No source contains `FACT` grammar, fixture markers, or serialized expected output.
- Regenerated and visually inspected valid source files in both demo locations. Counts: charter 4 pages/735 words; plan 5/729; risk register 4/580; CR-01 2/183; release plan 3/309; meeting notes 01 3/283; meeting notes 02 2/221; incremental CR-02 3/277; weekly TXT 434 words.
- Improved PDF parsing to reconstruct authored paragraph/row spans from wrapped layout lines, preserving meaningful exact provenance in multi-page PDFs.
- Fake extraction now supports several claims grounded in one natural paragraph and reads fingerprints from the separate oracle. A manifest regression checks every source document, type, claim/value, and evidence span while proving source text has no fixture grammar.
- Incremental proposal generation now suppresses repeated evidence whose normalized keyed value already matches the committed brief. CR-02 repeats deployment and general-availability context, but Review receives only beta-date, vendor-risk, and customer-pilot changes.
- Final verification: **36 passed, 1 paid test skipped in 25.41s**; frontend TypeScript/Vite production build passed. No hosted request was made.

## State-aware semantic correctness pass

- Added a corpus-scoped `canonical_entities` model with stable IDs, typed fields, aliases, supporting claim IDs, provenance, and relationship history. Claims persist their canonical entity and relationship.
- Each newly processed document still uses one structured analysis request. The hosted request now includes at most 12 compact, corpus-scoped candidate entities selected by lexical relevance and pgvector retrieval; it does not receive audit history, rendered briefs, or unrelated corpus state.
- The structured result includes document classification plus claim-level `canonical_entity_id`, relationship, and changed fields. Fake inference implements the same deterministic contract without network access.
- Deterministic reconciliation remains authoritative: exact IDs/aliases first, model-proposed identity only from supplied candidates, collision-safe new entity creation otherwise. Generic terms such as `status`, `owner`, `date`, `release`, and `launch` cannot establish identity by themselves.
- Conflict detection now requires the same canonical entity and exact fact dimension with different normalized values. Same values add support; compatible new dimensions enrich; formal change requests propose updates without mutating committed state before approval.
- Repeated case/punctuation variants normalize equivalently while raw evidence remains unchanged. Document metadata and rejected/non-project statements are omitted before canonicalization.
- Invalid provenance is represented only as an acknowledgeable evidence issue. It cannot enter canonical state, rules, proposed changes, draft facts, or the committed brief.
- Initial grounded non-conflicting facts default to Included and remain individually excludable/restorable in Extracted Information. The explicit initial commit gate and exception decisions remain durable.
- The Review UI labels conflict choices by value and source. Draft and committed briefs continue to share the concise grouped presentation, with provenance and technical data secondary.
- Regression coverage proves rich Atlas canonical behavior, repeated support, enrichment, one true launch conflict, one true rule finding, metadata rejection, punctuation/case equivalence, state-aware hosted prompt shape, invalid-provenance isolation, and preserved MCP/REST flow.
- Final deterministic backend result: **39 passed, 1 opt-in paid test skipped in 18.53s**. Frontend TypeScript and Vite production build passed (16 modules; 220.48 kB JS / 68.09 kB gzip). No hosted request was made.

## Hosted empty-corpus bootstrap regression correction

- Diagnosed hosted run `66ef14b5-8d97-4857-b9de-9e8787bc33fb` from persisted PostgreSQL state without contacting OpenAI. All eight documents were parsed and correctly classified, eight calls completed, and zero claim rows were produced. Historical storage did not retain raw response bodies or post-conversion counts, so it cannot retrospectively distinguish an empty model claim array from provider-adapter filtering; the 89 aggregate output tokens strongly support eight minimal classification-plus-empty-claims responses.
- Reworded the single-call hosted task to make extraction explicitly precede reconciliation. Empty canonical state is stated to be normal, canonical context is not an allow-list, and valid unmatched facts must be emitted as `new_entity` with a null canonical ID.
- Verified the closed Structured Outputs schema permits arrays of grounded claims and nullable canonical IDs. Provider-contract tests exercise new, supporting, enriching, and conflicting relationships through the real hosted conversion path.
- Confirmed the workflow bootstrap path creates a canonical entity from a grounded `new_entity` claim when candidate context is empty; provenance remains exact and the resulting facts populate Extracted Information and the Draft Brief.
- Added durable per-document `analysis_results` diagnostics: classification, provider/converted claim counts, relationships, proposed canonical IDs, provider discard reasons, and workflow discard reasons. Raw document/request content and secrets are not logged.
- Added an evidence-quality warning and pending evidence-issue gate when a non-empty analyzed run yields no extracted claim rows. The UI surfaces the warning on Analysis Run; it is not converted into a business finding or fabricated fact.
- Regression tests cover empty-state bootstrap, mixed relationship conversion, prompt wording, per-document diagnostics, and the zero-extraction gate. Existing rich Atlas expectations remain exactly one production-launch conflict and one unowned-high-risk finding.
- Verification: **45 passed, 1 paid test skipped in 40.37s**. Rich Atlas, incremental Atlas, Beacon, MCP, persistence, provenance, and isolation remain covered. Frontend TypeScript/Vite build passed (16 modules; 220.71 kB JS / 68.14 kB gzip). No paid request was made.

## Final Task 1 committed-state semantics correction

- A conflict resolved by selecting source value A or B now commits that selected value as the current brief item and removes the contradiction from the active `conflicts` section. The selected item retains the complete contradictory claim set, provenance, selected claim/value, human-review actor, decision timestamp, run ID, and optional note under one collapsed **Resolution & conflicting evidence** control.
- **Keep unresolved** remains an active conflict and is rendered explicitly as `UNRESOLVED`, with every value/source and the fact that no value was selected.
- **Exclude from brief** produces no current factual item or active conflict. Its decision, original claims, sources, timestamp, and run remain in `recent_changes` audit history.
- Approved deterministic findings are marked as acknowledged active findings. Rejected findings are removed from active findings and retained only in audit history.
- Evidence-validation issues remain reviewable system exceptions and can never enter any factual report section.
- REST and MCP continue to use the same commit implementation; MCP regression coverage now asserts that a selected conflict has no active conflict card while its audit evidence remains preserved.
- Final deterministic backend/MCP result: **47 passed, 1 opt-in paid test skipped in 27.99s**. Frontend TypeScript/Vite production build passed (16 modules; 221.67 kB JS / 68.37 kB gzip). No OpenAI call was made.
