import hashlib
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_session
from .models import Claim, Conflict, Corpus, Document, ProposedChange, Report, ReviewItem, RuleFinding, Run
from .rules import validate_ruleset

router = APIRouter(prefix="/api")


class CorpusCreate(BaseModel):
    name: str


class RunCreate(BaseModel):
    document_ids: list[UUID] | None = None
    kind: str = "initial"


class ReviewDecision(BaseModel):
    review_item_id: UUID
    decision: str
    note: str | None = None
    conflict_action: str | None = None
    selected_claim_id: UUID | None = None


class ReviewRequest(BaseModel):
    decisions: list[ReviewDecision]


class ClaimInclusion(BaseModel):
    included: bool


class RulesetUpdate(BaseModel):
    ruleset: dict


def as_dict(row, *fields):
    return {field: getattr(row, field) for field in fields}


async def scoped_run(session: AsyncSession, run_id: UUID) -> Run:
    run = await session.get(Run, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.post("/corpora", status_code=201)
async def create_corpus(body: CorpusCreate, session: AsyncSession = Depends(get_session)):
    corpus = Corpus(name=body.name)
    session.add(corpus)
    await session.commit()
    return {"id": corpus.id, "name": corpus.name}


@router.get("/corpora")
async def list_corpora(session: AsyncSession = Depends(get_session)):
    corpora = (await session.scalars(select(Corpus).order_by(Corpus.created_at))).all()
    return [as_dict(corpus, "id", "name", "created_at") for corpus in corpora]


@router.get("/corpora/{corpus_id}/ruleset")
async def get_ruleset(corpus_id: UUID, session: AsyncSession = Depends(get_session)):
    corpus = await session.get(Corpus, corpus_id)
    if not corpus:
        raise HTTPException(404, "Corpus not found")
    return corpus.ruleset


@router.put("/corpora/{corpus_id}/ruleset")
async def set_ruleset(corpus_id: UUID, body: RulesetUpdate, session: AsyncSession = Depends(get_session)):
    corpus = await session.get(Corpus, corpus_id)
    if not corpus:
        raise HTTPException(404, "Corpus not found")
    try:
        corpus.ruleset = validate_ruleset(body.ruleset)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await session.commit()
    return corpus.ruleset


async def _start_run(corpus_id: UUID, body: RunCreate, request: Request, session: AsyncSession):
    if body.kind not in {"initial", "incremental"}:
        raise HTTPException(422, "Run kind must be initial or incremental")
    query = select(Document).where(Document.corpus_id == corpus_id)
    if body.document_ids:
        query = query.where(Document.id.in_(body.document_ids))
    documents = (await session.scalars(query)).all()
    if not documents:
        raise HTTPException(400, "Corpus has no selected documents")
    if body.document_ids and len(documents) != len(set(body.document_ids)):
        raise HTTPException(400, "One or more documents do not belong to this corpus")
    if body.document_ids:
        by_id = {document.id: document for document in documents}
        documents = [by_id[document_id] for document_id in body.document_ids]
    else:
        documents.sort(key=lambda document: document.created_at)
    if body.kind == "incremental":
        processed = set((await session.scalars(select(Claim.document_id).where(Claim.corpus_id == corpus_id))).all())
        documents = [document for document in documents if document.id not in processed]
        if not documents:
            raise HTTPException(400, "Corpus has no new documents to process")
    report = await session.get(Report, corpus_id)
    corpus = await session.get(Corpus, corpus_id)
    active_provider = request.app.state.provider
    metrics = {"run_kind": body.kind, "base_report_version": report.version if report else 0,
               "provider": getattr(active_provider, "name", "unknown"), "model": getattr(active_provider, "model", None),
               "ruleset": corpus.ruleset, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
               "estimated_cost_usd": 0.0, "completed_llm": {"analyze": []}, "affected_scope": []}
    run = Run(corpus_id=corpus_id, document_ids=[str(d.id) for d in documents], metrics=metrics)
    session.add(run)
    await session.commit()
    initial = {"corpus_id": str(corpus_id), "run_id": str(run.id), "document_ids": run.document_ids, "run_kind": body.kind}
    await request.app.state.workflow.graph.ainvoke(initial, {"configurable": {"thread_id": str(run.id)}})
    await session.refresh(run)
    return run


@router.post("/corpora/{corpus_id}/documents", status_code=201)
async def upload_document(corpus_id: UUID, file: UploadFile, request: Request, auto_analyze: bool = False, session: AsyncSession = Depends(get_session)):
    if not await session.get(Corpus, corpus_id):
        raise HTTPException(404, "Corpus not found")
    content = await file.read()
    digest = hashlib.sha256(content).hexdigest()
    existing = await session.scalar(select(Document).where(Document.corpus_id == corpus_id, Document.sha256 == digest))
    if existing:
        return {"id": existing.id, "filename": existing.filename, "sha256": digest, "duplicate": True, "incremental_run_created": False, "incremental_run": None}
    document = Document(corpus_id=corpus_id, filename=file.filename or "document.txt", media_type=file.content_type or "application/octet-stream", sha256=digest, content=content)
    session.add(document)
    await session.commit()
    report = await session.get(Report, corpus_id)
    run = await _start_run(corpus_id, RunCreate(document_ids=[document.id], kind="incremental"), request, session) if auto_analyze and report else None
    return {"id": document.id, "filename": document.filename, "sha256": digest, "duplicate": False,
            "incremental_run_created": bool(run), "incremental_run": {"id": run.id, "status": run.status} if run else None}


@router.get("/corpora/{corpus_id}/documents")
async def list_documents(corpus_id: UUID, session: AsyncSession = Depends(get_session)):
    if not await session.get(Corpus, corpus_id):
        raise HTTPException(404, "Corpus not found")
    documents = (await session.scalars(select(Document).where(Document.corpus_id == corpus_id).order_by(Document.created_at))).all()
    return [as_dict(document, "id", "filename", "media_type", "sha256", "parse_status", "classification", "created_at") for document in documents]


@router.post("/corpora/{corpus_id}/runs", status_code=201)
async def create_run(corpus_id: UUID, body: RunCreate, request: Request, session: AsyncSession = Depends(get_session)):
    if not await session.get(Corpus, corpus_id):
        raise HTTPException(404, "Corpus not found")
    run = await _start_run(corpus_id, body, request, session)
    return {"id": run.id, "corpus_id": run.corpus_id, "status": run.status}


@router.get("/corpora/{corpus_id}/runs")
async def list_corpus_runs(corpus_id: UUID, session: AsyncSession = Depends(get_session)):
    if not await session.get(Corpus, corpus_id):
        raise HTTPException(404, "Corpus not found")
    runs = (await session.scalars(select(Run).where(Run.corpus_id == corpus_id).order_by(Run.created_at.desc()))).all()
    return [as_dict(run, "id", "corpus_id", "status", "created_at") for run in runs]


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, session: AsyncSession = Depends(get_session)):
    run = await scoped_run(session, run_id)
    return as_dict(run, "id", "corpus_id", "status", "document_ids", "stage_log", "metrics", "error")


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: UUID, request: Request, session: AsyncSession = Depends(get_session)):
    from langgraph.types import Command

    run = await scoped_run(session, run_id)
    if run.status not in {"waiting_review", "failed", "running"}:
        raise HTTPException(409, f"Run in status {run.status} cannot be resumed")
    await request.app.state.workflow.graph.ainvoke(Command(resume={"reviewed": True}), {"configurable": {"thread_id": str(run.id)}})
    await session.refresh(run)
    return {"id": run.id, "status": run.status}


@router.get("/runs/{run_id}/review-items")
async def get_review_items(run_id: UUID, session: AsyncSession = Depends(get_session)):
    run = await scoped_run(session, run_id)
    items = (await session.scalars(select(ReviewItem).where(ReviewItem.run_id == run.id).order_by(ReviewItem.item_type, ReviewItem.id))).all()
    return [{**as_dict(item, "id", "item_type", "target_id", "payload", "decision", "note"), "run_id": run.id} for item in items]


@router.post("/runs/{run_id}/review")
async def review_items(run_id: UUID, body: ReviewRequest, session: AsyncSession = Depends(get_session)):
    run = await scoped_run(session, run_id)
    if run.status != "waiting_review":
        raise HTTPException(409, "Run is not waiting for review")
    for decision in body.decisions:
        if decision.decision not in {"approved", "rejected"}:
            raise HTTPException(422, "Decision must be approved or rejected")
        item = await session.scalar(select(ReviewItem).where(ReviewItem.id == decision.review_item_id, ReviewItem.run_id == run.id, ReviewItem.corpus_id == run.corpus_id))
        if not item:
            raise HTTPException(404, f"Review item {decision.review_item_id} not found in run")
        if item.item_type == "conflict":
            action = decision.conflict_action or ("keep_unresolved" if decision.decision == "approved" else "exclude")
            if action not in {"keep_unresolved", "select_claim", "exclude"}:
                raise HTTPException(422, "Conflict action must be keep_unresolved, select_claim, or exclude")
            claim_ids = {claim.get("claim_id") for claim in item.payload.get("claims", [])}
            if action == "select_claim" and str(decision.selected_claim_id) not in claim_ids:
                raise HTTPException(422, "selected_claim_id must identify evidence in this conflict")
            item.payload = {**item.payload, "resolution": {"action": action, "selected_claim_id": str(decision.selected_claim_id) if decision.selected_claim_id else None}}
            item.decision = "rejected" if action == "exclude" else "approved"
        else:
            item.decision = decision.decision
        item.note = decision.note
        item.decided_at = datetime.now(timezone.utc)
    await session.commit()
    return {"updated": len(body.decisions), "resume_required": True}


@router.get("/corpora/{corpus_id}/report")
async def get_report(corpus_id: UUID, session: AsyncSession = Depends(get_session)):
    if not await session.get(Corpus, corpus_id):
        raise HTTPException(404, "Corpus not found")
    report = await session.get(Report, corpus_id)
    from .workflow import empty_report
    data = report.data if report else empty_report()
    if isinstance(data.get("status"), dict):
        data = {**data, "status": [data["status"]] if data["status"] else []}
    return data


@router.get("/runs/{run_id}/metrics")
async def get_metrics(run_id: UUID, session: AsyncSession = Depends(get_session)):
    run = await scoped_run(session, run_id)
    return {"metrics": run.metrics, "stages": run.stage_log}


@router.get("/runs/{run_id}/claims")
async def get_claims(run_id: UUID, session: AsyncSession = Depends(get_session)):
    run = await scoped_run(session, run_id)
    claims = (await session.scalars(select(Claim).where(Claim.run_id == run.id))).all()
    conflicts = (await session.scalars(select(Conflict).where(Conflict.run_id == run.id))).all()
    conflict_keys = {item.entity_key for item in conflicts}
    inclusion = (run.metrics or {}).get("claim_inclusion", {})
    return [{**as_dict(c, "id", "type", "entity_key", "canonical_entity_id", "relationship", "claim", "normalized_value", "provenance", "confidence", "grounded"), "conflicting": c.canonical_entity_id in conflict_keys, "inclusion_state": inclusion.get(str(c.id)), "included_in_draft": c.grounded and inclusion.get(str(c.id)) != "excluded"} for c in claims]


@router.post("/runs/{run_id}/claims/{claim_id}/inclusion")
async def set_claim_inclusion(run_id: UUID, claim_id: UUID, body: ClaimInclusion, session: AsyncSession = Depends(get_session)):
    run = await scoped_run(session, run_id)
    if run.status != "waiting_review" or (run.metrics or {}).get("run_kind") != "initial":
        raise HTTPException(409, "Draft fact inclusion can only change during initial review")
    claim = await session.scalar(select(Claim).where(Claim.id == claim_id, Claim.run_id == run.id, Claim.grounded.is_(True)))
    if not claim:
        raise HTTPException(404, "Grounded claim not found in run")
    metrics = dict(run.metrics or {})
    inclusion = dict(metrics.get("claim_inclusion", {}))
    if str(claim.id) not in inclusion:
        raise HTTPException(409, "This claim is not an individually selectable normal draft fact")
    inclusion[str(claim.id)] = "included" if body.included else "excluded"
    metrics["claim_inclusion"], run.metrics = inclusion, metrics
    await session.commit()
    return {"claim_id": claim.id, "inclusion_state": inclusion[str(claim.id)], "included_in_draft": body.included}


@router.get("/runs/{run_id}/draft")
async def get_draft(run_id: UUID, request: Request, session: AsyncSession = Depends(get_session)):
    run = await scoped_run(session, run_id)
    data = await request.app.state.workflow.build_draft(run.id)
    return {"run_id": run.id, "corpus_id": run.corpus_id, "status": "draft" if run.status != "completed" else "committed", "run_kind": (run.metrics or {}).get("run_kind"), "data": data}
