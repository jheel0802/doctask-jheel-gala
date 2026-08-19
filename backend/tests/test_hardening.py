from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from project_evidence.config import Settings
from project_evidence.llm import FakeLLMProvider
from project_evidence.main import create_app
from project_evidence.models import CanonicalEntity, Claim, Document
from project_evidence.embeddings import canonical_entity, entities_equivalent
from project_evidence.normalization import normalize_claim_value
from project_evidence.llm import DocumentAnalysis, ExtractedClaim, LLMResult
from project_evidence.atlas_ground_truth import DOCUMENT_TYPES, EXPECTED_CLAIMS
from project_evidence.parsing import parse_document


ROOT = Path(__file__).parents[2]


@pytest.mark.asyncio
async def test_atlas_ground_truth_manifest_drives_fake_without_document_markers():
    provider = FakeLLMProvider()
    for filename, document_type in DOCUMENT_TYPES.items():
        path = ROOT / "demo_data" / "atlas" / filename
        spans = parse_document(filename, path.read_bytes())
        result = await provider.analyze_document(filename=filename, untrusted_spans=spans)
        expected = [claim for entry in EXPECTED_CLAIMS[filename] for claim in entry["claims"]]
        actual = [(claim.type, claim.entity_key, claim.normalized_value) for claim in result.value.claims]
        assert result.value.document_type == document_type
        assert actual == expected
        assert all(claim.evidence in {span["text"] for span in spans} for claim in result.value.claims)
        source_text = " ".join(span["text"] for span in spans)
        assert "FACT " not in source_text and "fixture" not in source_text.lower()


async def upload(client, corpus_id, path, *, auto=False):
    return await client.post(
        f"/api/corpora/{corpus_id}/documents?auto_analyze={'true' if auto else 'false'}",
        files={"file": (path.name, path.read_bytes(), "text/plain")},
    )


async def resolve_and_commit(client, run_id, *, exclude_entity=None):
    claims = (await client.get(f"/api/runs/{run_id}/claims")).json()
    for claim in claims:
        if claim.get("inclusion_state") in {"pending", "included"}:
            await client.post(
                f"/api/runs/{run_id}/claims/{claim['id']}/inclusion",
                json={"included": claim["entity_key"] != exclude_entity},
            )
    items = (await client.get(f"/api/runs/{run_id}/review-items")).json()
    await client.post(f"/api/runs/{run_id}/review", json={"decisions": [
        {"review_item_id": item["id"], "decision": "approved"} for item in items
    ]})
    return await client.post(f"/api/runs/{run_id}/resume")


@pytest.fixture
async def context():
    provider = FakeLLMProvider()
    app = create_app(Settings(), provider)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client, provider, app


@pytest.mark.asyncio
async def test_ruleset_is_data_driven_disabled_rules_and_clean_zero_findings(context):
    client, _, _ = context
    corpus = (await client.post("/api/corpora", json={"name": "Config rules"})).json()
    ruleset = {
        "name": "Custom checklist",
        "rules": [
            {"id": "custom_green_attention", "title": "Green status needs confirmation.", "enabled": True,
             "fact_type": "status", "conditions": [{"field": "status", "op": "eq", "value": "green"}]},
            {"id": "disabled_medium_risk", "title": "Medium risk disabled.", "enabled": False,
             "fact_type": "risk", "conditions": [{"field": "severity", "op": "eq", "value": "medium"}]},
        ],
    }
    assert (await client.put(f"/api/corpora/{corpus['id']}/ruleset", json={"ruleset": ruleset})).status_code == 200
    paths = [ROOT / "demo_data/beacon/project_plan.txt", ROOT / "demo_data/beacon/status_report.txt"]
    ids = [(await upload(client, corpus["id"], path)).json()["id"] for path in paths]
    run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": ids})).json()
    items = (await client.get(f"/api/runs/{run['id']}/review-items")).json()
    assert [item["payload"]["rule_id"] for item in items] == ["custom_green_attention"]

    clean = (await client.post("/api/corpora", json={"name": "No enabled rules"})).json()
    await client.put(f"/api/corpora/{clean['id']}/ruleset", json={"ruleset": {"name": "Off", "rules": []}})
    doc = (await upload(client, clean["id"], ROOT / "demo_data/beacon/status_report.txt")).json()
    clean_run = (await client.post(f"/api/corpora/{clean['id']}/runs", json={"document_ids": [doc["id"]]})).json()
    assert not (await client.get(f"/api/runs/{clean_run['id']}/review-items")).json()


@pytest.mark.asyncio
async def test_vectors_semantic_alias_conflict_and_corpus_scope(context):
    client, _, app = context
    atlas = (await client.post("/api/corpora", json={"name": "Vector Atlas"})).json()
    atlas_paths = [ROOT / "demo_data/atlas/project_plan.pdf", ROOT / "demo_data/atlas/meeting_notes_01.docx"]
    ids = [(await upload(client, atlas["id"], path)).json()["id"] for path in atlas_paths]
    run = (await client.post(f"/api/corpora/{atlas['id']}/runs", json={"document_ids": ids})).json()
    items = (await client.get(f"/api/runs/{run['id']}/review-items")).json()
    conflict = next(item for item in items if item["item_type"] == "conflict")
    assert {claim["provenance"]["source_document"] for claim in conflict["payload"]["claims"]} == {"project_plan.pdf", "meeting_notes_01.docx"}
    assert {claim["normalized_value"]["date"] for claim in conflict["payload"]["claims"]} == {"2026-09-12", "2026-09-26"}
    async with app.state.database.sessions() as session:
        stored = (await session.scalars(select(Claim).where(Claim.run_id == run["id"]))).all()
        assert stored and all(claim.embedding is not None for claim in stored)

    beacon = (await client.post("/api/corpora", json={"name": "Vector Beacon"})).json()
    doc = (await upload(client, beacon["id"], ROOT / "demo_data/beacon/project_plan.txt")).json()
    beacon_run = (await client.post(f"/api/corpora/{beacon['id']}/runs", json={"document_ids": [doc["id"]]})).json()
    assert not any(item["item_type"] == "conflict" for item in (await client.get(f"/api/runs/{beacon_run['id']}/review-items")).json())

    exact = (await client.post("/api/corpora", json={"name": "Exact entities"})).json()
    first = b"The production launch target in the approved plan is September 12, 2026.\n"
    second = b"A revised planning draft lists the production launch target as September 19, 2026.\n"
    exact_ids = []
    for index, content in enumerate((first, second)):
        response = await client.post(f"/api/corpora/{exact['id']}/documents", files={"file": (f"plan_{index}.txt", content, "text/plain")})
        exact_ids.append(response.json()["id"])
    exact_run = (await client.post(f"/api/corpora/{exact['id']}/runs", json={"document_ids": exact_ids})).json()
    exact_items = (await client.get(f"/api/runs/{exact_run['id']}/review-items")).json()
    assert len([item for item in exact_items if item["item_type"] == "conflict"]) == 1


@pytest.mark.asyncio
async def test_classification_persisted_and_passed_to_extraction(context):
    client, provider, app = context
    corpus = (await client.post("/api/corpora", json={"name": "Classification"})).json()
    doc = (await upload(client, corpus["id"], ROOT / "demo_data/atlas/project_plan.pdf")).json()
    run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [doc["id"]]})).json()
    listed = (await client.get(f"/api/corpora/{corpus['id']}/documents")).json()
    assert listed[0]["classification"] == "project_plan"
    analysis = next(call for call in provider.calls if call["method"] == "analyze_document")
    assert analysis["filename"] == "project_plan.pdf"
    detail = (await client.get(f"/api/runs/{run['id']}")).json()
    assert detail["metrics"]["provider"] == "fake" and detail["metrics"]["model"] is None
    async with app.state.database.sessions() as session:
        assert (await session.get(Document, doc["id"])).classification == "project_plan"


@pytest.mark.asyncio
async def test_initial_inclusion_gate_and_exclusion(context):
    client, _, _ = context
    corpus = (await client.post("/api/corpora", json={"name": "Fact gate"})).json()
    doc = (await upload(client, corpus["id"], ROOT / "demo_data/beacon/project_plan.txt")).json()
    run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [doc["id"]]})).json()
    claims = (await client.get(f"/api/runs/{run['id']}/claims")).json()
    assert all(claim["inclusion_state"] == "included" for claim in claims)
    completed = await resolve_and_commit(client, run["id"], exclude_entity="field_pilot")
    assert completed.json()["status"] == "completed"
    report = (await client.get(f"/api/corpora/{corpus['id']}/report")).json()
    assert report["milestones"] == [] and report["owners"] == []
    assert report["deadlines"][0]["key"] == "customer_preview"


@pytest.mark.asyncio
async def test_document_arrival_trigger_and_recent_change_audit(context):
    client, _, _ = context
    corpus = (await client.post("/api/corpora", json={"name": "Arrival"})).json()
    initial_upload = await upload(client, corpus["id"], ROOT / "demo_data/atlas/project_plan.pdf", auto=True)
    assert initial_upload.json()["incremental_run_created"] is False
    initial = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [initial_upload.json()["id"]]})).json()
    assert (await resolve_and_commit(client, initial["id"])).json()["status"] == "completed"

    arrived = await upload(client, corpus["id"], ROOT / "demo_data/atlas/change_request_02.docx", auto=True)
    payload = arrived.json()
    assert payload["incremental_run_created"] is True
    run_id = payload["incremental_run"]["id"]
    items = (await client.get(f"/api/runs/{run_id}/review-items")).json()
    # This focused setup initially committed only the project plan, so the
    # repeated deployment context is genuinely new here (full Atlas suppresses it).
    assert len(items) == 4 and all(item["item_type"] == "change" for item in items)
    await client.post(f"/api/runs/{run_id}/review", json={"decisions": [{"review_item_id": item["id"], "decision": "approved"} for item in items]})
    assert (await client.post(f"/api/runs/{run_id}/resume")).json()["status"] == "completed"
    recent_items = (await client.get(f"/api/corpora/{corpus['id']}/report")).json()["recent_changes"]
    recent = next(item for item in recent_items if item["run_id"] == run_id and item["entity"] == "beta_release")
    assert recent["changed_at"] and recent["run_id"] == run_id and recent["entity"] == "beta_release"
    assert recent["old_value"]["normalized_value"]["date"] == "2026-08-28"
    assert recent["new_value"]["normalized_value"]["date"] == "2026-09-04"
    assert recent["source_documents"][0]["source_document"] == "change_request_02.docx"
    assert recent["source_documents"][0]["section"] == "Approved changes"


def test_demo_sources_are_natural_and_secret_docs_are_safe():
    sources = list((ROOT / "demo_data").rglob("*.txt"))
    assert sources and all("FACT " not in path.read_text() for path in sources)
    for relative in ("README.md", "PROGRESS.md", "Makefile", ".env.example"):
        text = (ROOT / relative).read_text()
        assert "export OPENAI_API_KEY" not in text
        assert "sk-" not in text
    assert ".env" in (ROOT / ".gitignore").read_text().splitlines()


def test_entity_equivalence_ignores_roles_without_merging_different_subjects():
    assert entities_equivalent("production_launch", "production rollout date")
    assert canonical_entity("production_launch_target") == canonical_entity("production rollout date")
    assert not entities_equivalent("project_status", "deployment_status")
    assert not entities_equivalent("vendor_capacity", "deployment_capacity")


def test_missing_owner_representations_have_one_canonical_value():
    variants = [None, "None", "null", "Unassigned", "not assigned", "N/A"]
    normalized = [normalize_claim_value({"severity": "high", "owner": value}) for value in variants]
    assert all(value == {"severity": "high", "owner": None} for value in normalized)


def test_textual_comparison_normalization_ignores_formatting_only():
    left = normalize_claim_value({"decision": "  The QA exit gate will require two independent reviewers.  "})
    right = normalize_claim_value({"decision": "the qa exit gate will require two independent reviewers"})
    assert left == right


@pytest.mark.asyncio
async def test_rich_atlas_builds_canonical_entities_without_false_conflicts(context):
    client, provider, app = context
    corpus = (await client.post("/api/corpora", json={"name": "Canonical Atlas"})).json()
    names = ["project_charter.pdf", "project_plan.pdf", "release_plan.docx", "meeting_notes_01.docx", "meeting_notes_02.docx", "weekly_status_01.txt", "risk_register.pdf", "change_request_01.pdf"]
    ids = [(await upload(client, corpus["id"], ROOT / "demo_data" / "atlas" / name)).json()["id"] for name in names]
    run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": ids})).json()
    items = (await client.get(f"/api/runs/{run['id']}/review-items")).json()
    claims = (await client.get(f"/api/runs/{run['id']}/claims")).json()
    assert [item["item_type"] for item in items].count("conflict") == 1
    assert [item["item_type"] for item in items].count("finding") == 1
    conflict = next(item for item in items if item["item_type"] == "conflict")
    assert conflict["payload"]["entity_key"] == "production_launch"
    assert conflict["payload"]["dimension"] == "date"
    assert {claim["normalized_value"]["date"] for claim in conflict["payload"]["claims"]} == {"2026-09-12", "2026-09-26"}
    assert not any(claim["normalized_value"].get("status") == "approved working record" for claim in claims)
    assert not any("ignore the project controls" in claim["claim"].lower() for claim in claims)
    async with app.state.database.sessions() as session:
        entities = (await session.scalars(select(CanonicalEntity).where(CanonicalEntity.corpus_id == corpus["id"]))).all()
        by_id = {entity.entity_id: entity for entity in entities}
        assert {"beta_release", "integration_rehearsal", "general_availability", "production_launch", "vendor_capacity", "data_migration", "api_stability", "credential_rotation", "qa_exit", "audit_log_export"} <= set(by_id)
        assert by_id["beta_release"].fields == {"date": "2026-08-28", "owner": "Maya Chen", "status": "planned"}
        assert len(by_id["beta_release"].claim_ids) >= 4
        assert len({item["source_document"] for item in by_id["beta_release"].provenance}) >= 3
    meeting_call = next(call for call in provider.calls if call["filename"] == "meeting_notes_01.docx")
    assert any(entity["entity_id"] == "production_launch" for entity in meeting_call["canonical_context"])


class MetadataClaimProvider(FakeLLMProvider):
    async def analyze_document(self, **kwargs):
        span = kwargs["untrusted_spans"][0]
        return LLMResult(value=DocumentAnalysis(document_type="project_plan", claims=[ExtractedClaim(
            type="status", entity_key="project", claim=span["text"], normalized_value={"status": "approved working record"},
            span_index=0, evidence=span["text"], confidence=0.99,
        )]))


@pytest.mark.asyncio
async def test_document_metadata_cannot_become_project_status():
    app = create_app(Settings(), MetadataClaimProvider())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            corpus = (await client.post("/api/corpora", json={"name": "Metadata isolation"})).json()
            document = (await client.post(f"/api/corpora/{corpus['id']}/documents", files={"file": ("metadata.txt", b"DOCUMENT STATUS Approved working record\n", "text/plain")})).json()
            run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [document["id"]]})).json()
            assert (await client.get(f"/api/runs/{run['id']}/claims")).json() == []
            assert (await client.get(f"/api/runs/{run['id']}/draft")).json()["data"]["status"] == []


class EmptyBootstrapContractProvider(FakeLLMProvider):
    async def analyze_document(self, **kwargs):
        assert kwargs["canonical_context"] == []
        spans = kwargs["untrusted_spans"]
        return LLMResult(value=DocumentAnalysis(document_type="project_plan", claims=[
            ExtractedClaim(type="milestone", entity_key="beta_release", claim=spans[0]["text"], normalized_value={"date": "2026-08-28", "status": "planned"}, span_index=0, evidence=spans[0]["text"], confidence=0.98, relationship="new_entity", canonical_entity_id=None),
            ExtractedClaim(type="owner", entity_key="beta_release", claim=spans[1]["text"], normalized_value={"owner": "Maya Chen"}, span_index=1, evidence=spans[1]["text"], confidence=0.98, relationship="new_entity", canonical_entity_id=None),
        ]), diagnostics={"provider_claim_count": 2, "converted_claim_count": 2, "provider_discarded": []})


@pytest.mark.asyncio
async def test_empty_canonical_state_bootstraps_grounded_entities_and_draft():
    app = create_app(Settings(), EmptyBootstrapContractProvider())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            corpus = (await client.post("/api/corpora", json={"name": "Bootstrap"})).json()
            content = b"Beta is planned for August 28, 2026.\nMaya Chen owns Beta.\n"
            document = (await client.post(f"/api/corpora/{corpus['id']}/documents", files={"file": ("plan.txt", content, "text/plain")})).json()
            run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [document['id']]})).json()
            claims = (await client.get(f"/api/runs/{run['id']}/claims")).json()
            assert len(claims) == 2 and all(claim["grounded"] for claim in claims)
            assert {claim["canonical_entity_id"] for claim in claims} == {"beta_release"}
            draft = (await client.get(f"/api/runs/{run['id']}/draft")).json()["data"]
            assert draft["milestones"] and draft["owners"]
            detail = (await client.get(f"/api/runs/{run['id']}")).json()
            result = detail["metrics"]["analysis_results"][document["id"]]
            assert result["converted_claim_count"] == 2 and result["workflow_discarded"] == []


class ZeroClaimProvider(FakeLLMProvider):
    async def analyze_document(self, **kwargs):
        return LLMResult(value=DocumentAnalysis(document_type="project_plan", claims=[]), diagnostics={"provider_claim_count": 0, "converted_claim_count": 0, "provider_discarded": []})


@pytest.mark.asyncio
async def test_zero_corpus_extraction_creates_visible_evidence_quality_gate():
    app = create_app(Settings(), ZeroClaimProvider())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            corpus = (await client.post("/api/corpora", json={"name": "Zero extraction"})).json()
            document = (await client.post(f"/api/corpora/{corpus['id']}/documents", files={"file": ("plan.txt", b"A substantial current project plan paragraph.\n", "text/plain")})).json()
            run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [document['id']]})).json()
            detail = (await client.get(f"/api/runs/{run['id']}")).json()
            assert detail["metrics"]["evidence_quality_warnings"][0]["code"] == "zero_grounded_claims"
            reviews = (await client.get(f"/api/runs/{run['id']}/review-items")).json()
            issue = next(item for item in reviews if item["item_type"] == "evidence_issue")
            assert issue["decision"] == "pending"
            assert "No grounded project facts" in issue["payload"]["message"]
