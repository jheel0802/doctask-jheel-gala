# Project Evidence Agent

A grounded document-analysis system for software project delivery.

It ingests related **PDF, DOCX, and TXT** documents, extracts structured facts with provenance, detects contradictions, evaluates deterministic rules, pauses for item-level human review, and maintains a human-approved **Project Status Brief**.

Built with **FastAPI, LangGraph, PostgreSQL/pgvector, React, and MCP**.

Uploaded source documents remain immutable. Human decisions update only the derived brief.

---

## Run the real application

### Prerequisites

- Docker Desktop with Compose
- `make`
- An OpenAI API key

Create local configuration:

```bash
cp .env.example .env
```

Set:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your-key-here
```

Then run:

```bash
make app
```

Open:

```text
http://localhost:3000
```

The hosted path uses OpenAI Structured Outputs for document classification and grounded fact extraction. Each newly processed document is handled separately rather than sending the whole corpus in one prompt.

`.env` is gitignored and must never be committed.

---

## Try it without an API key

For a deterministic local demo:

```bash
make demo
```

Open:

```text
http://localhost:3000/?demo=1
```

`make demo` forces `FakeLLMProvider`, so no API key or paid service is required.

The fake provider is fixture-specific and exists for repeatable testing and demonstration. It is not presented as general NLP.

To stop the stack without deleting PostgreSQL data:

```bash
make down
```

---

## What the system does

### Understand

- Parses PDF, DOCX, and TXT
- Classifies each document
- Extracts grounded project facts
- Stores provenance: source document, page/section, span, and evidence text
- Reconciles repeated evidence into corpus-scoped canonical entities

### Examine

- Detects contradictions across sources
- Evaluates configurable deterministic rules
- Produces findings with provenance
- Keeps unsupported evidence out of committed state

### Review

A reviewer can:

- include or exclude facts
- resolve a conflict to one source value
- leave a conflict unresolved
- reject or acknowledge findings
- approve or reject incremental updates

Rejecting one item does not discard the rest.

### Stay alive

Once a brief exists, new evidence follows a focused incremental path.

The system processes only newly arrived documents, determines what is affected, proposes updates, and leaves unrelated committed content unchanged.

---

## Workflow

```text
Documents
   ↓
Classify + extract
   ↓
Validate provenance
   ↓
Normalize + reconcile
   ↓
Detect conflicts + evaluate rules
   ↓
Draft Project Status Brief
   ↓
Human review
   ↓
Commit approved state
   ↓
New evidence
   ↓
Focused incremental update
```

The model interprets evidence. Deterministic application logic and explicit human decisions control authoritative state.

---

## Quick Atlas walkthrough

The deterministic Atlas demo shows the complete flow.

1. Select **Start fresh Atlas demo**.
2. Open **Analysis Run** to inspect visible stages, latency, calls, tokens, cost, and warnings.
3. Open **Extracted Info** to inspect facts and provenance.
4. Open **Draft Brief** to preview the uncommitted report.
5. Open **Review** to resolve the generated conflict and finding.
6. Select **Commit reviewed brief**.
7. Open **Status Brief** to inspect committed state and audit detail.
8. In **Corpus**, select **Add Atlas weekly update** to start a focused incremental run.
9. Review the proposed changes and confirm unrelated brief content remains unchanged.

Atlas includes realistic project prose, repeated supporting evidence, tentative language, irrelevant material, and a prompt-injection fixture.

A separate Beacon corpus provides a second document set and clean/no-finding path.

---

## Architecture

```text
React reviewer UI
        │
        ▼
FastAPI REST API ───────── MCP
        │                    │
        └────────┬───────────┘
                 ▼
          LangGraph workflow
                 │
      ┌──────────┼───────────┐
      ▼          ▼           ▼
   Parsing    LLM layer   Deterministic logic
 PDF/DOCX/TXT fake/real   provenance validation
                          normalization
                          conflict detection
                          rules
                          review state
                          commits
                 │
                 ▼
        PostgreSQL + pgvector
```

### Key design choice

The LLM is used for semantic work: classification and extraction.

Application code owns:

- IDs and hashes
- provenance validation
- canonical reconciliation authority
- conflict detection
- deterministic rules
- review decisions
- stale-write protection
- report commits
- call/cost limits

This keeps model output from directly becoming project truth.

---

## Agentic behavior

An **Analysis Run** is one durable LangGraph execution against one corpus.

PostgreSQL checkpoints use:

```text
thread_id = run_id
```

The workflow has real branch behavior, including:

- retry
- escalation
- review interrupt
- resume
- incremental affected-scope routing

A stopped run can resume using the same `run_id` without losing completed work.

Two concurrent runs remain isolated, including two runs against the same corpus.

---

## Tests

Run:

```bash
make install
make test
```

Tests require no API key and force `FakeLLMProvider`.

Current deterministic result:

**48 passed, 1 skipped**

The skipped test is the explicitly opt-in paid OpenAI smoke test.

The suite covers:

- restart and resume
- concurrent runs
- prompt injection inside source documents
- provenance validation
- incremental processing
- stale-report write protection
- conflict and review semantics
- deterministic rules
- no-key execution

Optional hosted smoke test:

```bash
make test-live-llm
```

---

## MCP

MCP exposes the same application behavior rather than duplicating workflow logic.

```bash
make mcp
```

or:

```bash
make mcp-http
```

Typical machine-driven flow:

```text
create_corpus
→ add_document
→ start_analysis
→ inspect evidence / draft / review
→ save review decisions
→ resume_analysis
→ get_committed_brief
```

The approval gate remains explicit in the machine interface.

---

## REST API

Interactive OpenAPI docs are available at:

```text
http://localhost:8000/docs
```

The API covers:

- corpora
- rules
- document upload
- analysis runs
- extracted claims
- fact inclusion
- draft briefs
- review items
- review decisions
- resume
- metrics
- committed briefs

---

## Rules and run limits

Each corpus owns a small configurable JSON ruleset.

Built-in examples include:

- `unowned_high_risk`
- `overdue_milestone`
- `red_project_status`

Supported conditions include:

- `eq`
- `missing`
- `not_in`
- `before_today`

Default guards:

```env
MAX_RETRIES_PER_STAGE=1
MAX_LLM_CALLS_PER_RUN=16
MAX_RUN_COST_USD=0.25
```

Each stage records status, latency, calls, tokens, estimated cost, and sanitized errors.

---

## Supported inputs and domain

### Formats

- PDF
- DOCX
- TXT

### Intended domain

Software project delivery evidence, including:

- project plans
- status reports
- meeting notes
- change requests
- risk logs
- related documents describing the same project

The included Atlas and Beacon corpora are synthetic and safe to share.

---

## Known limitations

- OCR is not implemented.
- The UI is single-user and unauthenticated.
- Workflow execution is synchronous in the API process.
- There is no worker queue or production deployment setup.
- TXT provenance has section/span information but no native page number.
- A process death before a hosted provider response is durably committed may repeat that in-flight call; completed calls are not repeated.
- The deterministic fake provider is fixture-specific.
- Arbitrary domains and additional hosted providers are out of scope.

---

## Useful commands

```bash
make app       # production-style Docker Compose stack
make demo      # deterministic no-key demo
make test      # deterministic test suite
make api       # FastAPI development server
make frontend  # Vite development server
make mcp       # MCP over stdio
make mcp-http  # MCP over HTTP
make down      # stop Compose without deleting PostgreSQL data
```

---

## Summary

Project Evidence Agent maintains a grounded, reviewable project brief from a changing pile of documents.

**Models interpret evidence; deterministic code and explicit human decisions control authoritative state.**
