import hashlib
import json
import time
import re
from datetime import datetime, timezone
from typing import Any, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import delete, select

from .config import Settings
from .llm import LLMProvider
from .models import CanonicalEntity, Claim, Conflict, Corpus, Document, ProposedChange, Report, ReviewItem, RuleFinding, Run
from .parsing import parse_document
from .embeddings import EmbeddingProvider
from .rules import rule_matches
from .normalization import is_rejected_or_non_project_fact, merge_compatible_values, normalize_claim_value


class WorkflowState(TypedDict, total=False):
    corpus_id: str
    run_id: str
    run_kind: str
    document_ids: list[str]
    affected_scope: list[str]
    invalid_claim_ids: list[str]
    validation_attempt: int
    failure_stage: str | None
    failure_reason: str | None


SECTION_BY_TYPE = {"status": "status", "milestone": "milestones", "deadline": "deadlines", "owner": "owners", "risk": "risks", "decision": "decisions"}


def empty_report() -> dict[str, Any]:
    return {"status": [], "milestones": [], "deadlines": [], "owners": [], "risks": [], "decisions": [], "conflicts": [], "findings": [], "recent_changes": []}


def report_items(data: dict[str, Any], section: str) -> list[dict[str, Any]]:
    value = data.get(section, [])
    if isinstance(value, list):
        return value
    return [value] if isinstance(value, dict) and value else []


def upsert_report_item(data: dict[str, Any], section: str, item_key: str, item: dict[str, Any]) -> dict[str, Any] | None:
    items = report_items(data, section)
    old = next((value for value in items if value.get("item_key", value.get("key")) == item_key), None)
    data[section] = [value for value in items if value.get("item_key", value.get("key")) != item_key] + [item]
    return old


DIMENSION_WORDS = {"date", "owner", "status", "severity", "deadline", "target"}


def normalized_entity_id(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", value.lower().replace("_", " "))
    while tokens and tokens[-1] in DIMENSION_WORDS:
        tokens.pop()
    return "_".join(tokens) or "entity"


def entity_type_for(fact_type: str) -> str:
    return "delivery" if fact_type in {"milestone", "deadline", "owner"} else fact_type


def human_label(entity_id: str) -> str:
    return entity_id.replace("_", " ").title()


def is_document_metadata(item, span: dict[str, Any]) -> bool:
    text = f"{item.claim} {item.evidence}".lower()
    section = str(span.get("section") or "").lower()
    return any(phrase in text for phrase in ("approved working record", "document classification", "document status", "controlled document")) or section in {"document metadata", "revision history"}


def failure_guidance(reason: str) -> tuple[str, str]:
    lowered = reason.lower()
    if "run cost limit" in lowered or "llm call limit" in lowered:
        return "The application's configured per-run guard was reached.", "Raise the applicable application guard if appropriate, then resume this run_id."
    if "http 400" in lowered:
        return "OpenAI rejected the request payload or structured-output schema; this is not a spending-budget failure.", "Inspect the safe provider detail, correct the request/schema issue, then resume this run_id."
    if "authentication failed" in lowered:
        return "OpenAI rejected the configured API credentials.", "Correct the API key in .env, restart the backend, then resume this run_id."
    if "rate or billing limit" in lowered:
        return "OpenAI reported a rate or account-billing limit.", "Check the OpenAI project limits/billing status, then resume this run_id."
    return "The hosted provider failed before the document analysis completed.", "Resolve the provider issue, then resume this run_id."


class Workflow:
    def __init__(self, database, provider: LLMProvider, settings: Settings, checkpointer, embedding_provider: EmbeddingProvider):
        self.db, self.provider, self.settings = database, provider, settings
        self.embedding_provider = embedding_provider
        self.graph = self._build_graph().compile(checkpointer=checkpointer)

    async def _canonical_context(self, session, corpus_id: UUID, spans: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
        entities = (await session.scalars(select(CanonicalEntity).where(CanonicalEntity.corpus_id == corpus_id))).all()
        if not entities:
            return []
        document_terms = set(re.findall(r"[a-z0-9]+", " ".join(span["text"] for span in spans).lower()))
        weak = {"launch", "release", "milestone", "readiness", "status", "date", "target", "plan", "train", "owner"}
        def score(entity):
            terms = set(re.findall(r"[a-z0-9]+", " ".join([entity.entity_id, entity.label, *entity.aliases]).lower()))
            return len((terms - weak) & document_terms), len(terms & document_terms)
        selected = sorted(entities, key=score, reverse=True)
        relevant = [entity for entity in selected if score(entity) > (0, 0)][:limit]
        document_vector = (await self.embedding_provider.embed([" ".join(span["text"] for span in spans)]))[0]
        semantic_claims = (await session.scalars(select(Claim).where(Claim.corpus_id == corpus_id, Claim.canonical_entity_id.is_not(None), Claim.embedding.is_not(None)).order_by(Claim.embedding.cosine_distance(document_vector)).limit(limit))).all()
        semantic_ids = list(dict.fromkeys(claim.canonical_entity_id for claim in semantic_claims))
        by_id = {entity.entity_id: entity for entity in entities}
        for entity_id in semantic_ids:
            entity = by_id.get(entity_id)
            if entity and entity not in relevant and len(relevant) < limit:
                relevant.append(entity)
        if not relevant:
            relevant = selected[: min(4, limit)]
        return [{"entity_id": entity.entity_id, "type": entity.entity_type, "label": entity.label, "aliases": entity.aliases, "fields": entity.fields} for entity in relevant]

    async def _reconcile_claim(self, session, *, corpus_id: UUID, run_id: UUID, document: Document, item, provenance: dict[str, Any], context: list[dict[str, Any]]) -> tuple[str, str]:
        proposed = item.canonical_entity_id
        context_ids = {entry["entity_id"] for entry in context}
        raw_id = normalized_entity_id(item.entity_key)
        entities = (await session.scalars(select(CanonicalEntity).where(CanonicalEntity.corpus_id == corpus_id))).all()
        target = next((entity for entity in entities if raw_id == entity.entity_id or raw_id in {normalized_entity_id(alias) for alias in entity.aliases}), None)
        if not target and proposed in context_ids:
            target = next((entity for entity in entities if entity.entity_id == proposed), None)
        if not target:
            entity_id, suffix = raw_id, 2
            existing_ids = {entity.entity_id for entity in entities}
            while entity_id in existing_ids:
                entity_id, suffix = f"{raw_id}_{suffix}", suffix + 1
            target = CanonicalEntity(corpus_id=corpus_id, entity_id=entity_id, entity_type=entity_type_for(item.type), label=human_label(entity_id), aliases=[item.entity_key], fields={}, claim_ids=[], provenance=[], history=[])
            session.add(target); await session.flush()
        normalized = normalize_claim_value(item.normalized_value)
        existing_fields = dict(target.fields or {})
        changed = {key: {"existing": existing_fields[key], "candidate": value} for key, value in normalized.items() if key in existing_fields and existing_fields[key] != value}
        new_fields = {key: value for key, value in normalized.items() if key not in existing_fields}
        if changed:
            relationship = "updates_existing" if document.classification == "change_request" else "conflicts_with_existing"
        elif new_fields:
            relationship = "enriches_existing" if existing_fields else "new_entity"
        else:
            relationship = "supports_existing"
        if relationship not in {"conflicts_with_existing", "updates_existing"}:
            target.fields = {**existing_fields, **normalized}
        target.aliases = list(dict.fromkeys([*(target.aliases or []), item.entity_key]))
        target.provenance = [*(target.provenance or []), provenance]
        target.history = [*(target.history or []), {"run_id": str(run_id), "document_id": str(document.id), "relationship": relationship, "fields": normalized, "changed_fields": changed}]
        return target.entity_id, relationship

    async def _log(self, run_id: UUID, stage: str, started: float, status: str = "completed", *, usage: dict[str, Any] | None = None, detail: dict[str, Any] | None = None, error: str | None = None) -> None:
        event = {"stage": stage, "status": status, "latency_ms": round((time.perf_counter() - started) * 1000, 3), "llm_calls": (usage or {}).get("calls", 0), "input_tokens": (usage or {}).get("input_tokens", 0), "output_tokens": (usage or {}).get("output_tokens", 0), "estimated_cost_usd": (usage or {}).get("estimated_cost_usd", 0.0), "error": error, "at": datetime.now(timezone.utc).isoformat(), **(detail or {})}
        async with self.db.sessions() as session:
            run = await session.get(Run, run_id)
            run.stage_log = [*(run.stage_log or []), event]
            await session.commit()

    async def _metrics(self, run_id: UUID) -> dict[str, Any]:
        async with self.db.sessions() as session:
            return dict((await session.get(Run, run_id)).metrics or {})

    def _guard(self, metrics: dict[str, Any]) -> str | None:
        if metrics.get("llm_calls", 0) >= self.settings.max_llm_calls_per_run:
            return f"LLM call limit ({self.settings.max_llm_calls_per_run}) reached"
        if metrics.get("estimated_cost_usd", 0.0) >= self.settings.max_run_cost_usd:
            return f"Run cost limit (${self.settings.max_run_cost_usd:.2f}) reached"
        return None

    def _safe_error(self, exc: Exception) -> str:
        message = f"{type(exc).__name__}: {exc}"
        if self.settings.openai_api_key:
            message = message.replace(self.settings.openai_api_key, "[REDACTED]")
        return message

    async def prepare(self, state: WorkflowState) -> dict[str, Any]:
        started, run_id, corpus_id = time.perf_counter(), UUID(state["run_id"]), UUID(state["corpus_id"])
        async with self.db.sessions() as session:
            run = await session.get(Run, run_id)
            if not run or run.corpus_id != corpus_id:
                raise ValueError("run_id does not belong to corpus_id")
            run.status, run.error = "running", None
            await session.commit()
        await self._log(run_id, "prepare", started)
        return {"validation_attempt": 0, "failure_stage": None, "failure_reason": None}

    async def parse(self, state: WorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        async with self.db.sessions() as session:
            docs = (await session.scalars(select(Document).where(Document.id.in_([UUID(value) for value in state["document_ids"]]), Document.corpus_id == UUID(state["corpus_id"])))).all()
            for doc in docs:
                if doc.parse_status != "parsed":
                    doc.spans, doc.parse_status = parse_document(doc.filename, doc.content), "parsed"
            await session.commit()
        await self._log(UUID(state["run_id"]), "parse", started, detail={"documents": len(docs)})
        return {}

    async def analyze(self, state: WorkflowState) -> dict[str, Any]:
        stage = "analyze"
        started, run_id = time.perf_counter(), UUID(state["run_id"])
        metrics = await self._metrics(run_id)
        completed = set(metrics.get("completed_llm", {}).get(stage, []))
        usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}
        async with self.db.sessions() as session:
            loaded = (await session.scalars(select(Document).where(Document.id.in_([UUID(value) for value in state["document_ids"]]), Document.corpus_id == UUID(state["corpus_id"])))).all()
            by_id = {str(doc.id): doc for doc in loaded}
            docs = [by_id[value] for value in state["document_ids"] if value in by_id]
        for doc in docs:
            if str(doc.id) in completed:
                continue
            if reason := self._guard(metrics):
                await self._log(run_id, stage, started, "failed", usage=usage, error=reason)
                return {"failure_stage": stage, "failure_reason": reason}
            try:
                async with self.db.sessions() as session:
                    canonical_context = await self._canonical_context(session, UUID(state["corpus_id"]), doc.spans)
                result = await self.provider.analyze_document(filename=doc.filename, untrusted_spans=doc.spans, canonical_context=canonical_context)
                async with self.db.sessions() as session:
                    stored = await session.get(Document, doc.id)
                    stored.classification = result.value.document_type
                    workflow_discarded = []
                    for item in result.value.claims:
                        if is_rejected_or_non_project_fact(item.type, item.claim, item.normalized_value):
                            workflow_discarded.append({"entity_key": item.entity_key, "reason": "rejected_or_non_project_fact"})
                            continue
                        valid_index = 0 <= item.span_index < len(doc.spans)
                        if valid_index and is_document_metadata(item, doc.spans[item.span_index]):
                            workflow_discarded.append({"entity_key": item.entity_key, "reason": "document_metadata"})
                            continue
                        provenance = {"source_document": doc.filename, "document_id": str(doc.id), "page": doc.spans[item.span_index].get("page") if valid_index else None, "section": doc.spans[item.span_index].get("section") if valid_index else None, "span_index": item.span_index, "evidence": item.evidence}
                        normalized_value = normalize_claim_value(item.normalized_value)
                        fingerprint = hashlib.sha256(f"{item.type}|{item.entity_key}|{json.dumps(normalized_value, sort_keys=True)}|{item.span_index}".encode()).hexdigest()
                        exists = await session.scalar(select(Claim.id).where(Claim.run_id == run_id, Claim.document_id == doc.id, Claim.fingerprint == fingerprint))
                        if not exists:
                            immediately_grounded = valid_index and item.evidence == doc.spans[item.span_index]["text"]
                            if immediately_grounded:
                                canonical_id, relationship = await self._reconcile_claim(session, corpus_id=UUID(state["corpus_id"]), run_id=run_id, document=stored, item=item, provenance=provenance, context=canonical_context)
                            else:
                                canonical_id, relationship = None, "invalid_provenance"
                            claim = Claim(corpus_id=UUID(state["corpus_id"]), run_id=run_id, document_id=doc.id, type=item.type, entity_key=item.entity_key, canonical_entity_id=canonical_id, relationship=relationship, claim=item.claim, normalized_value=normalized_value, provenance=provenance, confidence=item.confidence, grounded=False, fingerprint=fingerprint)
                            if immediately_grounded:
                                claim.embedding = (await self.embedding_provider.embed([f"{item.type} {item.entity_key} {item.claim}"]))[0]
                            session.add(claim); await session.flush()
                            if canonical_id:
                                entity = await session.scalar(select(CanonicalEntity).where(CanonicalEntity.corpus_id == UUID(state["corpus_id"]), CanonicalEntity.entity_id == canonical_id))
                                entity.claim_ids = list(dict.fromkeys([*(entity.claim_ids or []), str(claim.id)]))
                    # Classification, claims, usage, and completed-work marker share one commit.
                    run = await session.get(Run, run_id, with_for_update=True)
                    metrics = dict(run.metrics or {})
                    completed_metrics = {key: list(value) for key, value in metrics.get("completed_llm", {"analyze": []}).items()}
                    completed_metrics.setdefault("analyze", []).append(str(doc.id))
                    metrics["completed_llm"] = completed_metrics
                    analysis_results = dict(metrics.get("analysis_results", {}))
                    analysis_results[str(doc.id)] = {
                        "filename": doc.filename, "document_type": result.value.document_type,
                        "converted_claim_count": len(result.value.claims),
                        "relationships": [item.relationship for item in result.value.claims],
                        "canonical_entity_ids": [item.canonical_entity_id for item in result.value.claims],
                        "provider": result.diagnostics, "workflow_discarded": workflow_discarded,
                    }
                    metrics["analysis_results"] = analysis_results
                    metrics["llm_calls"] += result.calls
                    metrics["input_tokens"] += result.input_tokens
                    metrics["output_tokens"] += result.output_tokens
                    metrics["estimated_cost_usd"] += result.estimated_cost_usd
                    run.metrics = metrics
                    await session.commit()
            except Exception as exc:
                reason = self._safe_error(exc)
                await self._log(run_id, stage, started, "failed", usage=usage, error=reason)
                return {"failure_stage": stage, "failure_reason": reason}
            completed.add(str(doc.id))
            usage["calls"] += result.calls
            usage["input_tokens"] += result.input_tokens
            usage["output_tokens"] += result.output_tokens
            usage["estimated_cost_usd"] += result.estimated_cost_usd
            if metrics.get("estimated_cost_usd", 0) > self.settings.max_run_cost_usd:
                reason = f"Run cost limit (${self.settings.max_run_cost_usd:.2f}) exceeded"
                await self._log(run_id, stage, started, "failed", usage=usage, error=reason)
                return {"failure_stage": stage, "failure_reason": reason}
        await self._log(run_id, stage, started, usage=usage, detail={"documents_reused": len(docs) - usage["calls"]})
        return {"failure_stage": None, "failure_reason": None}

    def llm_route(self, state: WorkflowState) -> str: return "fail" if state.get("failure_reason") else "continue"

    async def fail_gracefully(self, state: WorkflowState) -> dict[str, Any]:
        started, run_id = time.perf_counter(), UUID(state["run_id"])
        reason = state.get("failure_reason") or "Unknown workflow error"
        likely_reason, user_action = failure_guidance(reason)
        async with self.db.sessions() as session:
            run = await session.get(Run, run_id)
            run.status = "failed"
            run.error = {"stage": state.get("failure_stage"), "message": reason, "likely_reason": likely_reason, "retry_possible": True, "user_action": user_action}
            await session.commit()
        await self._log(run_id, "fail_gracefully", started, "failed", error=reason)
        return {}

    async def await_retry(self, state: WorkflowState) -> dict[str, Any]:
        interrupt({"run_id": state["run_id"], "reason": state.get("failure_reason"), "retry_possible": True})
        async with self.db.sessions() as session:
            run = await session.get(Run, UUID(state["run_id"]))
            run.status, run.error = "running", None
            await session.commit()
        return {"failure_reason": None}

    def retry_stage(self, state: WorkflowState) -> str: return "analyze"

    async def validate(self, state: WorkflowState) -> dict[str, Any]:
        started, invalid = time.perf_counter(), []
        async with self.db.sessions() as session:
            claims = (await session.scalars(select(Claim).where(Claim.run_id == UUID(state["run_id"]), Claim.grounded.is_(False)))).all()
            docs = {doc.id: doc for doc in (await session.scalars(select(Document).where(Document.corpus_id == UUID(state["corpus_id"])))).all()}
            for claim in claims:
                doc, provenance = docs.get(claim.document_id), claim.provenance
                index = provenance.get("span_index")
                valid = bool(doc and provenance.get("document_id") == str(doc.id) and provenance.get("source_document") == doc.filename and isinstance(index, int) and 0 <= index < len(doc.spans) and provenance.get("evidence") == doc.spans[index]["text"])
                claim.grounded = valid
                if valid:
                    claim.embedding = (await self.embedding_provider.embed([f"{claim.type} {claim.entity_key}"]))[0]
                if not valid: invalid.append(str(claim.id))
            grounded_count = await session.scalar(select(Claim.id).where(Claim.run_id == UUID(state["run_id"]), Claim.grounded.is_(True)).limit(1))
            extracted_count = await session.scalar(select(Claim.id).where(Claim.run_id == UUID(state["run_id"])).limit(1))
            run = await session.get(Run, UUID(state["run_id"]))
            metrics = dict(run.metrics or {})
            warnings = [warning for warning in metrics.get("evidence_quality_warnings", []) if warning.get("code") != "zero_grounded_claims"]
            if not grounded_count and not extracted_count and state.get("document_ids"):
                warnings.append({"code": "zero_grounded_claims", "message": "No grounded project facts were extracted from this corpus; inspect analysis before committing.", "documents": len(state["document_ids"])})
            metrics["evidence_quality_warnings"], run.metrics = warnings, metrics
            await session.commit()
        attempt = state.get("validation_attempt", 0) + 1
        await self._log(UUID(state["run_id"]), "validate", started, detail={"attempt": attempt, "invalid": len(invalid)})
        return {"invalid_claim_ids": invalid, "validation_attempt": attempt}

    def validation_route(self, state: WorkflowState) -> str:
        if not state.get("invalid_claim_ids"): return "continue"
        return "retry" if state["validation_attempt"] <= self.settings.max_retries_per_stage else "escalate"

    async def prepare_retry(self, state: WorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        async with self.db.sessions() as session:
            invalid = [UUID(value) for value in state["invalid_claim_ids"]]
            document_ids = set((await session.scalars(select(Claim.document_id).where(Claim.id.in_(invalid)))).all())
            await session.execute(delete(Claim).where(Claim.id.in_(invalid)))
            run = await session.get(Run, UUID(state["run_id"]))
            metrics = dict(run.metrics)
            completed = {key: list(value) for key, value in metrics.get("completed_llm", {"analyze": []}).items()}
            completed["analyze"] = [value for value in completed.get("analyze", []) if UUID(value) not in document_ids]
            metrics["completed_llm"], run.metrics = completed, metrics
            await session.commit()
        await self._log(UUID(state["run_id"]), "retry_invalid", started, detail={"claims": len(invalid)})
        return {"invalid_claim_ids": []}

    async def escalate(self, state: WorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        async with self.db.sessions() as session:
            exists = await session.scalar(select(RuleFinding.id).where(RuleFinding.run_id == UUID(state["run_id"]), RuleFinding.rule_id == "invalid_provenance"))
            if not exists:
                session.add(RuleFinding(corpus_id=UUID(state["corpus_id"]), run_id=UUID(state["run_id"]), rule_id="invalid_provenance", data={"message": "One or more claims failed grounding after retry and cannot be committed.", "claim_ids": state["invalid_claim_ids"]}))
            await session.commit()
        await self._log(UUID(state["run_id"]), "escalate_invalid", started, detail={"claims": len(state["invalid_claim_ids"])})
        return {}

    async def compute_scope(self, state: WorkflowState) -> dict[str, Any]:
        started, run_id = time.perf_counter(), UUID(state["run_id"])
        async with self.db.sessions() as session:
            claims = (await session.scalars(select(Claim).where(Claim.run_id == run_id, Claim.grounded.is_(True)))).all()
            scope = sorted({f"{claim.type}:{claim.canonical_entity_id or normalized_entity_id(claim.entity_key)}" for claim in claims})
            run = await session.get(Run, run_id)
            metrics = dict(run.metrics); metrics["affected_scope"] = scope; run.metrics = metrics
            await session.commit()
        await self._log(run_id, "compute_affected_scope", started, detail={"scope": scope})
        return {"affected_scope": scope}

    async def detect_conflicts(self, state: WorkflowState) -> dict[str, Any]:
        started, run_id, corpus_id = time.perf_counter(), UUID(state["run_id"]), UUID(state["corpus_id"])
        scope = set(state.get("affected_scope", []))
        async with self.db.sessions() as session:
            run = await session.get(Run, run_id)
            await session.execute(delete(Conflict).where(Conflict.run_id == run_id))
            current = (await session.scalars(select(Claim).where(Claim.run_id == run_id, Claim.grounded.is_(True)))).all()
            affected_entities = {claim.canonical_entity_id for claim in current if claim.canonical_entity_id and claim.relationship == "conflicts_with_existing"}
            claims = (await session.scalars(select(Claim).where(Claim.corpus_id == corpus_id, Claim.grounded.is_(True), Claim.canonical_entity_id.in_(affected_entities)))).all() if affected_entities else []
            groups: dict[tuple[str, str], list[Claim]] = {}
            for claim in claims:
                groups.setdefault((entity_type_for(claim.type), claim.canonical_entity_id), []).append(claim)
            count = 0
            for (fact_type, key), values in groups.items():
                dimensions = sorted({dimension for value in values for dimension in value.normalized_value})
                conflicting = [dimension for dimension in dimensions if len({json.dumps(normalize_claim_value(value.normalized_value).get(dimension), sort_keys=True) for value in values if dimension in value.normalized_value}) > 1]
                for dimension in conflicting:
                    dimension_values = [value for value in values if dimension in value.normalized_value]
                    conflict_type = "deadline" if dimension == "date" and any(value.type == "deadline" for value in dimension_values) else dimension_values[0].type
                    snapshots = [{"claim_id": str(value.id), "claim": value.claim, "normalized_value": value.normalized_value, "provenance": value.provenance} for value in dimension_values]
                    session.add(Conflict(corpus_id=corpus_id, run_id=run_id, type=conflict_type, entity_key=key, data={"dimension": dimension, "claims": snapshots})); count += 1
            await session.commit()
        await self._log(run_id, "detect_conflicts", started, detail={"conflicts": count, "scope_size": len(scope)})
        return {}

    async def evaluate_rules(self, state: WorkflowState) -> dict[str, Any]:
        started, run_id, corpus_id = time.perf_counter(), UUID(state["run_id"]), UUID(state["corpus_id"])
        scope = set(state.get("affected_scope", []))
        async with self.db.sessions() as session:
            run = await session.get(Run, run_id)
            rules = (run.metrics or {}).get("ruleset", {}).get("rules", [])
            await session.execute(delete(RuleFinding).where(RuleFinding.run_id == run_id, RuleFinding.rule_id != "invalid_provenance"))
            # Findings for an incremental run describe the newly arrived evidence.
            # Re-evaluating older, superseded claims would create a stale finding.
            claims = (await session.scalars(select(Claim).where(Claim.run_id == run_id, Claim.grounded.is_(True)))).all()
            count, seen = 0, set()
            for claim in claims:
                canonical_id = claim.canonical_entity_id or normalized_entity_id(claim.entity_key)
                if f"{claim.type}:{canonical_id}" not in scope: continue
                for rule in rules:
                    if rule_matches(rule, claim.type, claim.normalized_value):
                        finding_key = (rule["id"], canonical_id)
                        if finding_key in seen:
                            continue
                        seen.add(finding_key)
                        session.add(RuleFinding(corpus_id=corpus_id, run_id=run_id, rule_id=rule["id"], data={"message": rule["title"], "claim_id": str(claim.id), "entity_key": canonical_id, "relevant_values": claim.normalized_value, "provenance": claim.provenance})); count += 1
            await session.commit()
        await self._log(run_id, "evaluate_rules", started, detail={"findings": count})
        return {}

    async def propose_changes(self, state: WorkflowState) -> dict[str, Any]:
        started, run_id, corpus_id = time.perf_counter(), UUID(state["run_id"]), UUID(state["corpus_id"])
        scope = set(state.get("affected_scope", []))
        async with self.db.sessions() as session:
            run = await session.get(Run, run_id)
            report = await session.get(Report, corpus_id)
            current_data = report.data if report else empty_report()
            await session.execute(delete(ProposedChange).where(ProposedChange.run_id == run_id))
            conflicts = (await session.scalars(select(Conflict).where(Conflict.run_id == run_id))).all()
            conflict_keys = {(conflict.type, conflict.entity_key) for conflict in conflicts}
            conflict_claim_ids = {claim["claim_id"] for conflict in conflicts for claim in conflict.data.get("claims", [])}
            claims = (await session.scalars(select(Claim).where(Claim.run_id == run_id, Claim.grounded.is_(True)))).all()
            groups: dict[tuple[str, str], list[Claim]] = {}
            for claim in claims:
                canonical_id = claim.canonical_entity_id or normalized_entity_id(claim.entity_key)
                if f"{claim.type}:{canonical_id}" not in scope or str(claim.id) in conflict_claim_ids:
                    continue
                key = canonical_id
                groups.setdefault((claim.type, key), []).append(claim)
            for (fact_type, key), values in groups.items():
                representative = values[0]
                merged = merge_compatible_values([value.normalized_value for value in values])
                section = SECTION_BY_TYPE[fact_type]
                current = next((item for item in report_items(current_data, section) if item.get("item_key", item.get("key")) == key), None)
                if (run.metrics or {}).get("run_kind") == "incremental" and current and normalize_claim_value(current.get("normalized_value", {})) == normalize_claim_value(merged):
                    continue
                session.add(ProposedChange(
                    corpus_id=corpus_id, run_id=run_id, section=section, item_key=key,
                    value={"claim_id": str(representative.id), "claim_ids": [str(value.id) for value in values], "key": key,
                           "claim": representative.claim, "normalized_value": merged},
                    provenance=[value.provenance for value in values],
                ))
            await session.commit()
        await self._log(run_id, "propose_changes", started)
        return {}

    async def create_review_items(self, state: WorkflowState) -> dict[str, Any]:
        started, run_id, corpus_id = time.perf_counter(), UUID(state["run_id"]), UUID(state["corpus_id"])
        async with self.db.sessions() as session:
            run = await session.get(Run, run_id)
            zero_warning = next((warning for warning in (run.metrics or {}).get("evidence_quality_warnings", []) if warning.get("code") == "zero_grounded_claims"), None)
            if zero_warning:
                exists = await session.scalar(select(ReviewItem.id).where(ReviewItem.run_id == run_id, ReviewItem.item_type == "evidence_issue", ReviewItem.target_id == run_id))
                if not exists:
                    session.add(ReviewItem(corpus_id=corpus_id, run_id=run_id, item_type="evidence_issue", target_id=run_id, payload={"rule_id": "zero_grounded_claims", **zero_warning}))
            review_models = [(Conflict, "conflict"), (RuleFinding, "finding")]
            if (run.metrics or {}).get("run_kind") == "incremental":
                review_models.append((ProposedChange, "change"))
            for model, item_type in review_models:
                for item in (await session.scalars(select(model).where(model.run_id == run_id))).all():
                    actual_type = "evidence_issue" if isinstance(item, RuleFinding) and item.rule_id == "invalid_provenance" else item_type
                    exists = await session.scalar(select(ReviewItem.id).where(ReviewItem.run_id == run_id, ReviewItem.item_type == actual_type, ReviewItem.target_id == item.id))
                    if not exists:
                        if isinstance(item, Conflict):
                            payload = {"type": item.type, "entity_key": item.entity_key, **item.data}
                        elif isinstance(item, RuleFinding):
                            payload = {"rule_id": item.rule_id, **item.data}
                        else:
                            payload = item.data if hasattr(item, "data") else {"section": item.section, "item_key": item.item_key, "value": item.value, "provenance": item.provenance}
                        session.add(ReviewItem(corpus_id=corpus_id, run_id=run_id, item_type=actual_type, target_id=item.id, payload=payload))
            await session.flush()
            metrics = dict(run.metrics or {})
            if metrics.get("run_kind") == "initial":
                inclusion = dict(metrics.get("claim_inclusion", {}))
                conflict_claim_ids = {
                    claim["claim_id"]
                    for conflict in (await session.scalars(select(Conflict).where(Conflict.run_id == run_id))).all()
                    for claim in conflict.data.get("claims", [])
                }
                changes = (await session.scalars(select(ProposedChange).where(ProposedChange.run_id == run_id))).all()
                for change in changes:
                    for claim_id in change.value.get("claim_ids", [change.value.get("claim_id")]):
                        if claim_id and claim_id not in conflict_claim_ids:
                            inclusion.setdefault(claim_id, "included")
                metrics["claim_inclusion"] = inclusion
                metrics["initial_commit_gate"] = True
                run.metrics = metrics
            pending = await session.scalar(select(ReviewItem.id).where(ReviewItem.run_id == run_id, ReviewItem.decision == "pending").limit(1))
            unresolved_facts = "pending" in (run.metrics or {}).get("claim_inclusion", {}).values()
            initial_gate = (run.metrics or {}).get("run_kind") == "initial" and (run.metrics or {}).get("initial_commit_gate")
            run.status = "waiting_review" if pending or unresolved_facts or initial_gate else "running"
            await session.commit()
        await self._log(run_id, "create_review_items", started)
        return {}

    async def review_route(self, state: WorkflowState) -> str:
        async with self.db.sessions() as session:
            pending = await session.scalar(select(ReviewItem.id).where(ReviewItem.run_id == UUID(state["run_id"]), ReviewItem.decision == "pending").limit(1))
            run = await session.get(Run, UUID(state["run_id"]))
            unresolved_facts = "pending" in (run.metrics or {}).get("claim_inclusion", {}).values()
        initial_gate = (run.metrics or {}).get("run_kind") == "initial" and (run.metrics or {}).get("initial_commit_gate")
        return "review" if pending or unresolved_facts or initial_gate else "complete"

    async def await_review(self, state: WorkflowState) -> dict[str, Any]:
        interrupt({"run_id": state["run_id"], "reason": "item_level_review_required"})
        async with self.db.sessions() as session:
            pending = await session.scalar(select(ReviewItem.id).where(ReviewItem.run_id == UUID(state["run_id"]), ReviewItem.decision == "pending").limit(1))
            run = await session.get(Run, UUID(state["run_id"]))
            unresolved_facts = "pending" in (run.metrics or {}).get("claim_inclusion", {}).values()
        if pending or unresolved_facts: interrupt({"run_id": state["run_id"], "reason": "review_items_or_fact_inclusions_still_pending"})
        return {}

    async def _provenance_valid(self, session, provenance: dict[str, Any], corpus_id: UUID) -> bool:
        document_id = provenance.get("document_id")
        if not document_id: return False
        document, index = await session.get(Document, UUID(document_id)), provenance.get("span_index")
        return bool(document and document.corpus_id == corpus_id and provenance.get("source_document") == document.filename and isinstance(index, int) and 0 <= index < len(document.spans) and provenance.get("evidence") == document.spans[index]["text"])

    async def build_draft(self, run_id: UUID) -> dict[str, Any]:
        async with self.db.sessions() as session:
            run = await session.get(Run, run_id)
            if not run: raise ValueError("Run not found")
            report = await session.get(Report, run.corpus_id)
            data = json.loads(json.dumps({**empty_report(), **(report.data if report else {})}))
            data["status"] = report_items(data, "status")
            inclusion = (run.metrics or {}).get("claim_inclusion", {})
            changes = (await session.scalars(select(ProposedChange).where(ProposedChange.run_id == run_id))).all()
            for change in changes:
                claim_ids = change.value.get("claim_ids", [change.value.get("claim_id")])
                if claim_ids and all(inclusion.get(claim_id) == "excluded" for claim_id in claim_ids): continue
                item = {**change.value, "item_key": change.item_key, "provenance": change.provenance}
                upsert_report_item(data, change.section, change.item_key, item)
            reviews = (await session.scalars(select(ReviewItem).where(ReviewItem.run_id == run_id, ReviewItem.item_type.in_(["conflict", "finding"])))).all()
            for review in reviews:
                if review.decision == "rejected":
                    continue
                if review.item_type == "finding":
                    key = f"{review.payload.get('rule_id')}:{review.payload.get('entity_key', review.target_id)}"
                    upsert_report_item(data, "findings", key, {**review.payload, "item_key": key, "provenance": [review.payload.get("provenance")]})
                    continue
                claims = review.payload.get("claims", [])
                provenances = [claim.get("provenance") for claim in claims if claim.get("provenance")]
                resolution = review.payload.get("resolution", {"action": "pending"})
                selected = next((claim for claim in claims if claim.get("claim_id") == resolution.get("selected_claim_id")), None)
                if selected:
                    key = review.payload.get("entity_key")
                    upsert_report_item(data, SECTION_BY_TYPE[review.payload.get("type")], key, {
                        "key": key, "item_key": key, "claim": selected.get("claim"), "normalized_value": selected.get("normalized_value"),
                        "provenance": provenances, "human_resolution": resolution,
                    })
                else:
                    key = f"{review.payload.get('type')}:{review.payload.get('entity_key')}"
                    upsert_report_item(data, "conflicts", key, {**review.payload, "item_key": key, "resolution": resolution, "provenance": provenances})
            return data

    async def commit_approved(self, state: WorkflowState) -> dict[str, Any]:
        started, run_id, corpus_id = time.perf_counter(), UUID(state["run_id"]), UUID(state["corpus_id"])
        async with self.db.sessions() as session:
            report, run = await session.get(Report, corpus_id, with_for_update=True), await session.get(Run, run_id)
            if (run.metrics or {}).get("run_kind") == "initial":
                approved = (await session.scalars(select(ProposedChange).where(ProposedChange.run_id == run_id))).all()
            else:
                approved = (await session.execute(select(ProposedChange).join(ReviewItem, (ReviewItem.target_id == ProposedChange.id) & (ReviewItem.run_id == ProposedChange.run_id)).where(ProposedChange.run_id == run_id, ReviewItem.item_type == "change", ReviewItem.decision == "approved"))).scalars().all()
            if report and report.version != (run.metrics or {}).get("base_report_version", 0):
                run.status = "failed"; run.error = {"stage": "commit_approved", "message": "Report changed after this run started.", "likely_reason": "Another run committed to the same corpus.", "retry_possible": False, "user_action": "Start a new incremental run against the current report."}
                await session.commit()
                await self._log(run_id, "commit_approved", started, "failed", error=run.error["message"])
                return {}
            if not report:
                report = Report(corpus_id=corpus_id, data=empty_report(), version=0); session.add(report); await session.flush()
            data, committed = {**empty_report(), **(report.data or {})}, []
            data["status"] = report_items(data, "status")
            inclusion = (run.metrics or {}).get("claim_inclusion", {})
            for change in approved:
                claim_ids = change.value.get("claim_ids", [change.value.get("claim_id")])
                if (run.metrics or {}).get("run_kind") == "initial" and not any(inclusion.get(claim_id) == "included" for claim_id in claim_ids): continue
                if not change.provenance or not all([await self._provenance_valid(session, item, corpus_id) for item in change.provenance]): continue
                item = {**change.value, "item_key": change.item_key, "provenance": change.provenance}
                old_value = upsert_report_item(data, change.section, change.item_key, item)
                canonical = await session.scalar(select(CanonicalEntity).where(CanonicalEntity.corpus_id == corpus_id, CanonicalEntity.entity_id == change.item_key))
                if canonical:
                    canonical.fields = {**(canonical.fields or {}), **item.get("normalized_value", {})}
                    canonical.history = [*(canonical.history or []), {"run_id": str(run_id), "relationship": "human_approved_update", "fields": item.get("normalized_value", {}), "source_documents": change.provenance}]
                committed.append({"changed_at": datetime.now(timezone.utc).isoformat(), "run_id": str(run_id), "section": change.section, "entity": change.item_key, "old_value": old_value, "new_value": item, "source_documents": change.provenance})
                if change.section == "risks":
                    before_findings = report_items(data, "findings")
                    retained = [finding for finding in before_findings if finding.get("entity_key") != change.item_key or rule_matches({"enabled": True, "fact_type": "risk", "conditions": [{"field": "severity", "op": "eq", "value": "high"}, {"field": "owner", "op": "missing"}]}, "risk", item.get("normalized_value", {}))]
                    if len(retained) != len(before_findings):
                        removed = [finding for finding in before_findings if finding not in retained]
                        data["findings"] = retained
                        committed.append({"changed_at": datetime.now(timezone.utc).isoformat(), "run_id": str(run_id), "section": "findings", "entity": change.item_key, "old_value": removed, "new_value": None, "source_documents": change.provenance})
            reviewed_exceptions = (await session.scalars(select(ReviewItem).where(ReviewItem.run_id == run_id, ReviewItem.decision != "pending", ReviewItem.item_type.in_(["conflict", "finding"])))).all()
            for review in reviewed_exceptions:
                candidates = [claim.get("provenance") for claim in review.payload.get("claims", [])] if review.item_type == "conflict" else [review.payload.get("provenance")]
                provenances = [item for item in candidates if item]
                if provenances and not all([await self._provenance_valid(session, item, corpus_id) for item in provenances]): continue
                review_decision = {"decision": review.decision, "decided_at": review.decided_at.isoformat() if review.decided_at else None,
                                   "run_id": str(run_id), "actor": "human_review", "note": review.note}
                if review.item_type == "conflict":
                    claims = review.payload.get("claims", [])
                    resolution = {**review.payload.get("resolution", {"action": "keep_unresolved"}), **review_decision}
                    reports = []
                    for claim in claims:
                        normalized = claim.get("normalized_value", {})
                        rendered = next(iter(normalized.values())) if len(normalized) == 1 else json.dumps(normalized, sort_keys=True)
                        reports.append(f"{claim.get('provenance', {}).get('source_document', 'Source')} reports {rendered}")
                    key = f"{review.payload.get('type')}:{review.payload.get('entity_key')}"
                    unresolved = resolution.get("action") == "keep_unresolved"
                    selected = next((claim for claim in claims if claim.get("claim_id") == resolution.get("selected_claim_id")), None)
                    item = {"key": review.payload.get("entity_key"), "type": review.payload.get("type"), "claims": claims,
                            "summary": f"{review.payload.get('entity_key', 'Value').replace('_', ' ').title()} is unresolved. {'; '.join(reports)}.",
                            "resolution": resolution, "item_key": key, "provenance": provenances}
                    # A resolved or excluded conflict is audit history, not an
                    # active conflict in the human-approved project state.
                    data["conflicts"] = [value for value in report_items(data, "conflicts") if value.get("item_key", value.get("key")) != key]
                    if selected:
                        selected_section = SECTION_BY_TYPE[review.payload.get("type")]
                        selected_key = review.payload.get("entity_key")
                        selected_item = {"key": selected_key, "item_key": selected_key, "claim": selected.get("claim"),
                                         "normalized_value": selected.get("normalized_value"), "provenance": provenances,
                                         "human_resolution": {**resolution, "selected_claim_id": selected.get("claim_id"), "selected_value": selected.get("normalized_value"), "all_conflicting_claims": claims}}
                        old_selected = upsert_report_item(data, selected_section, selected_key, selected_item)
                        canonical = await session.scalar(select(CanonicalEntity).where(CanonicalEntity.corpus_id == corpus_id, CanonicalEntity.entity_id == selected_key))
                        if canonical:
                            canonical.fields = {**(canonical.fields or {}), **selected.get("normalized_value", {})}
                            canonical.history = [*(canonical.history or []), {"run_id": str(run_id), "relationship": "human_conflict_resolution", "selected_claim_id": selected.get("claim_id"), "fields": selected.get("normalized_value", {})}]
                        committed.append({"changed_at": datetime.now(timezone.utc).isoformat(), "run_id": str(run_id), "section": selected_section,
                                          "entity": selected_key, "old_value": old_selected, "new_value": selected_item, "source_documents": provenances,
                                          "human_resolution": resolution})
                    elif unresolved:
                        old_conflict = upsert_report_item(data, "conflicts", key, item)
                        committed.append({"changed_at": datetime.now(timezone.utc).isoformat(), "run_id": str(run_id), "section": "conflicts", "entity": key,
                                          "old_value": old_conflict, "new_value": item, "source_documents": provenances, "human_resolution": resolution})
                    else:
                        committed.append({"changed_at": datetime.now(timezone.utc).isoformat(), "run_id": str(run_id), "section": "conflicts_audit", "entity": key,
                                          "old_value": item, "new_value": None, "source_documents": provenances, "human_resolution": resolution,
                                          "all_conflicting_claims": claims})
                else:
                    key = f"{review.payload.get('rule_id', 'finding')}:{review.payload.get('entity_key', review.target_id)}"
                    item = {**review.payload, "item_key": key, "provenance": provenances, "acknowledged": review.decision == "approved", "review_decision": review_decision}
                    data["findings"] = [value for value in report_items(data, "findings") if value.get("item_key", value.get("key")) != key]
                    if review.decision == "approved":
                        old_finding = upsert_report_item(data, "findings", key, item)
                        committed.append({"changed_at": datetime.now(timezone.utc).isoformat(), "run_id": str(run_id), "section": "findings", "entity": key,
                                          "old_value": old_finding, "new_value": item, "source_documents": provenances, "review_decision": review_decision})
                    else:
                        committed.append({"changed_at": datetime.now(timezone.utc).isoformat(), "run_id": str(run_id), "section": "findings_audit", "entity": key,
                                          "old_value": item, "new_value": None, "source_documents": provenances, "review_decision": review_decision})
            data["recent_changes"] = [*data.get("recent_changes", []), *committed]
            report.data, report.version, run.status = data, report.version + 1, "completed"
            await session.commit()
        await self._log(run_id, "commit_approved", started, detail={"changes": len(committed)})
        return {}

    async def finalize(self, state: WorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        async with self.db.sessions() as session:
            run = await session.get(Run, UUID(state["run_id"])); run.status = "completed"; await session.commit()
        await self._log(UUID(state["run_id"]), "finalize", started)
        return {}

    def _build_graph(self):
        graph = StateGraph(WorkflowState)
        for name, node in (("prepare", self.prepare), ("parse", self.parse), ("analyze", self.analyze), ("fail_gracefully", self.fail_gracefully), ("await_retry", self.await_retry), ("validate", self.validate), ("prepare_retry", self.prepare_retry), ("escalate", self.escalate), ("compute_scope", self.compute_scope), ("detect_conflicts", self.detect_conflicts), ("evaluate_rules", self.evaluate_rules), ("propose_changes", self.propose_changes), ("create_review_items", self.create_review_items), ("await_review", self.await_review), ("commit_approved", self.commit_approved), ("finalize", self.finalize)):
            graph.add_node(name, node)
        graph.add_edge(START, "prepare"); graph.add_edge("prepare", "parse"); graph.add_edge("parse", "analyze")
        graph.add_conditional_edges("analyze", self.llm_route, {"continue": "validate", "fail": "fail_gracefully"})
        graph.add_edge("fail_gracefully", "await_retry")
        graph.add_conditional_edges("await_retry", self.retry_stage, {"analyze": "analyze"})
        graph.add_conditional_edges("validate", self.validation_route, {"continue": "compute_scope", "retry": "prepare_retry", "escalate": "escalate"})
        graph.add_edge("prepare_retry", "analyze"); graph.add_edge("escalate", "compute_scope"); graph.add_edge("compute_scope", "detect_conflicts"); graph.add_edge("detect_conflicts", "evaluate_rules"); graph.add_edge("evaluate_rules", "propose_changes"); graph.add_edge("propose_changes", "create_review_items")
        graph.add_conditional_edges("create_review_items", self.review_route, {"review": "await_review", "complete": "finalize"})
        graph.add_edge("await_review", "commit_approved"); graph.add_edge("commit_approved", END); graph.add_edge("finalize", END)
        return graph
