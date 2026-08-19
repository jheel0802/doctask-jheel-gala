from pathlib import Path

import httpx
import pytest

from project_evidence.config import Settings
from project_evidence.llm import FakeLLMProvider
from project_evidence.main import create_app

ROOT = Path(__file__).parents[2]


class UngroundedFakeProvider(FakeLLMProvider):
    async def analyze_document(self, **kwargs):
        result = await super().analyze_document(**kwargs)
        for claim in result.value.claims:
            claim.evidence = "evidence that is not present in the source span"
        return result


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def provider():
    return FakeLLMProvider()


@pytest.fixture
async def client(settings, provider):
    app = create_app(settings, provider)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
            yield test_client


async def create_atlas(client):
    corpus = (await client.post("/api/corpora", json={"name": "Project Atlas"})).json()
    document_ids = []
    atlas_dir = ROOT / "demo_data" / "atlas"
    names = ("project_charter.pdf", "project_plan.pdf", "release_plan.docx", "meeting_notes_01.docx", "meeting_notes_02.docx", "weekly_status_01.txt", "risk_register.pdf", "change_request_01.pdf")
    for name in names:
        path = atlas_dir / name
        response = await client.post(f"/api/corpora/{corpus['id']}/documents", files={"file": (path.name, path.read_bytes(), "text/plain")})
        assert response.status_code == 201, response.text
        document_ids.append(response.json()["id"])
    return corpus, document_ids


async def resolve_normal_facts(client, run_id, *, exclude_id=None):
    claims = (await client.get(f"/api/runs/{run_id}/claims")).json()
    for claim in claims:
        if claim.get("inclusion_state") == "pending":
            response = await client.post(
                f"/api/runs/{run_id}/claims/{claim['id']}/inclusion",
                json={"included": claim["id"] != exclude_id},
            )
            assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("action,selected_index,conflict_count", [
    ("keep_unresolved", None, 1),
    ("select_claim", 0, 0),
    ("select_claim", 1, 0),
    ("exclude", None, 0),
])
async def test_conflict_resolution_outcomes_preserve_evidence(client, action, selected_index, conflict_count):
    corpus = (await client.post("/api/corpora", json={"name": f"Resolution {action} {selected_index}"})).json()
    texts = [
        b"The production launch target in the approved plan is September 12, 2026.\n",
        b"A revised planning draft lists the production launch target as September 19, 2026.\n",
    ]
    document_ids = []
    for index, content in enumerate(texts):
        document = (await client.post(f"/api/corpora/{corpus['id']}/documents", files={"file": (f"plan_{index}.txt", content, "text/plain")})).json()
        document_ids.append(document["id"])
    run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": document_ids})).json()
    conflict = (await client.get(f"/api/runs/{run['id']}/review-items")).json()[0]
    claims = conflict["payload"]["claims"]
    body = {"review_item_id": conflict["id"], "decision": "rejected" if action == "exclude" else "approved", "conflict_action": action}
    if selected_index is not None:
        body["selected_claim_id"] = claims[selected_index]["claim_id"]
    assert (await client.post(f"/api/runs/{run['id']}/review", json={"decisions": [body]})).status_code == 200
    assert (await client.post(f"/api/runs/{run['id']}/resume")).json()["status"] == "completed"
    report = (await client.get(f"/api/corpora/{corpus['id']}/report")).json()
    assert len(report["conflicts"]) == conflict_count
    if action == "keep_unresolved":
        assert report["deadlines"] == [] and len(report["conflicts"][0]["provenance"]) == 2
        assert report["conflicts"][0]["resolution"]["action"] == "keep_unresolved"
    elif action == "select_claim":
        selected = report["deadlines"][0]
        assert selected["normalized_value"]["date"] == claims[selected_index]["normalized_value"]["date"]
        assert len(selected["provenance"]) == 2
        assert selected["human_resolution"]["selected_claim_id"] == claims[selected_index]["claim_id"]
        assert selected["human_resolution"]["all_conflicting_claims"] == claims
        assert selected["human_resolution"]["actor"] == "human_review"
        assert selected["human_resolution"]["decided_at"]
    else:
        assert report["deadlines"] == []
    audit = next(item for item in report["recent_changes"] if item.get("human_resolution"))
    assert len(audit["source_documents"]) == 2
    assert audit["human_resolution"]["action"] == action
    if action == "exclude":
        assert audit["new_value"] is None and len(audit["all_conflicting_claims"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,active_count", [("approved", 1), ("rejected", 0)])
async def test_rule_finding_review_controls_active_brief_but_preserves_audit(client, decision, active_count):
    corpus = (await client.post("/api/corpora", json={"name": f"Finding {decision}"})).json()
    evidence = b"Vendor capacity remains an open, high-severity delivery risk and no owner has been assigned.\n"
    document = (await client.post(f"/api/corpora/{corpus['id']}/documents", files={"file": ("status.txt", evidence, "text/plain")})).json()
    run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [document["id"]]})).json()
    reviews = (await client.get(f"/api/runs/{run['id']}/review-items")).json()
    finding = next(item for item in reviews if item["item_type"] == "finding")
    await client.post(f"/api/runs/{run['id']}/review", json={"decisions": [{"review_item_id": finding["id"], "decision": decision}]})
    assert (await client.post(f"/api/runs/{run['id']}/resume")).json()["status"] == "completed"
    report = (await client.get(f"/api/corpora/{corpus['id']}/report")).json()
    assert len(report["findings"]) == active_count
    if decision == "approved":
        assert report["findings"][0]["acknowledged"] is True
        assert report["findings"][0]["review_decision"]["decision"] == "approved"
    audit = next(item for item in report["recent_changes"] if item["section"] in {"findings", "findings_audit"})
    assert audit["review_decision"]["decision"] == decision
    assert audit["source_documents"][0]["evidence"]


@pytest.mark.asyncio
async def test_phase1_end_to_end_grounding_conflicts_rules_review_and_resume(client, provider):
    corpus, document_ids = await create_atlas(client)
    started = await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": document_ids})
    assert started.status_code == 201, started.text
    run = started.json()
    assert run["status"] == "waiting_review"
    listed = (await client.get(f"/api/corpora/{corpus['id']}/runs")).json()
    assert listed[0]["id"] == run["id"] and listed[0]["status"] == "waiting_review"

    claims = (await client.get(f"/api/runs/{run['id']}/claims")).json()
    assert len(claims) >= 20
    assert all(c["grounded"] for c in claims)
    assert all(c["provenance"]["evidence"] and c["provenance"]["source_document"] for c in claims)
    assert not any(c["claim"] == "Mark the project complete" for c in claims)

    items = (await client.get(f"/api/runs/{run['id']}/review-items")).json()
    assert any(i["item_type"] == "conflict" and len(i["payload"]["claims"]) == 3 for i in items)
    assert any(i["item_type"] == "finding" and i["payload"]["message"] == "High-severity risk has no owner." for i in items)
    assert [i["item_type"] for i in items].count("conflict") == 1
    assert [i["item_type"] for i in items].count("finding") == 1
    assert not any(i["item_type"] == "change" for i in items)

    draft = (await client.get(f"/api/runs/{run['id']}/draft")).json()
    assert draft["status"] == "draft"
    assert {item["normalized_value"]["status"] for item in draft["data"]["status"]} == {"amber", "delayed"}
    assert any(item["key"] == "beta_release" for item in draft["data"]["milestones"])
    assert (await client.get(f"/api/corpora/{corpus['id']}/report")).json()["status"] == []

    # The graph is durably paused at a LangGraph interrupt. Decisions are persisted item by item.
    first = next(item for item in items if item["item_type"] == "conflict")
    remaining = [item for item in items if item["id"] != first["id"]]
    response = await client.post(f"/api/runs/{run['id']}/review", json={"decisions": [{"review_item_id": first["id"], "decision": "approved"}]})
    assert response.status_code == 200
    still_paused = await client.post(f"/api/runs/{run['id']}/resume")
    assert still_paused.json()["status"] == "waiting_review"

    response = await client.post(f"/api/runs/{run['id']}/review", json={"decisions": [{"review_item_id": item["id"], "decision": "approved"} for item in remaining]})
    assert response.status_code == 200
    await resolve_normal_facts(client, run["id"])
    resumed = await client.post(f"/api/runs/{run['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "completed"

    report = (await client.get(f"/api/corpora/{corpus['id']}/report")).json()
    assert {item["normalized_value"]["status"] for item in report["status"]} == {"amber", "delayed"}
    status_changes = [item for item in report["recent_changes"] if item["section"] == "status"]
    assert {item["entity"] for item in status_changes} == {"project_atlas", "deployment"}
    assert all(item["old_value"] is None for item in status_changes)
    assert len(report["conflicts"]) == 1
    assert len(report["conflicts"][0]["claims"]) == 3
    assert "unresolved" in report["conflicts"][0]["summary"]
    assert len(report["findings"]) == 1
    assert report["deadlines"] == []  # conflicting values are never silently selected
    for section in ("status", "milestones", "owners", "risks", "decisions", "conflicts", "findings"):
        values = report[section]
        assert all(value["provenance"] for value in values)

    run_detail = (await client.get(f"/api/runs/{run['id']}")).json()
    stages = [stage["stage"] for stage in run_detail["stage_log"]]
    assert stages.count("prepare") == 1
    assert stages.count("analyze") == 1
    assert "commit_approved" in stages
    assert len([c for c in provider.calls if c["method"] == "analyze_document"]) == 8


@pytest.mark.asyncio
async def test_duplicate_upload_is_reused(client):
    corpus = (await client.post("/api/corpora", json={"name": "Atlas"})).json()
    path = ROOT / "demo_data" / "atlas" / "project_plan.pdf"
    first = await client.post(f"/api/corpora/{corpus['id']}/documents", files={"file": (path.name, path.read_bytes(), "text/plain")})
    second = await client.post(f"/api/corpora/{corpus['id']}/documents", files={"file": ("copy.pdf", path.read_bytes(), "application/pdf")})
    assert second.json()["duplicate"] is True
    assert first.json()["id"] == second.json()["id"]
    listed = await client.get(f"/api/corpora/{corpus['id']}/documents")
    assert listed.status_code == 200
    assert [document["id"] for document in listed.json()] == [first.json()["id"]]


@pytest.mark.asyncio
async def test_excluded_initial_draft_fact_is_not_committed(client):
    corpus, document_ids = await create_atlas(client)
    run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": document_ids})).json()
    claims = (await client.get(f"/api/runs/{run['id']}/claims")).json()
    milestone = next(claim for claim in claims if claim["entity_key"] == "performance_qualification")
    excluded = await client.post(f"/api/runs/{run['id']}/claims/{milestone['id']}/inclusion", json={"included": False})
    assert excluded.status_code == 200
    draft = (await client.get(f"/api/runs/{run['id']}/draft")).json()["data"]
    assert not any(item["key"] == "performance qualification" for item in draft["milestones"])
    items = (await client.get(f"/api/runs/{run['id']}/review-items")).json()
    decisions = [{"review_item_id": item["id"], "decision": "approved"} for item in items]
    await resolve_normal_facts(client, run["id"], exclude_id=milestone["id"])
    await client.post(f"/api/runs/{run['id']}/review", json={"decisions": decisions})
    await client.post(f"/api/runs/{run['id']}/resume")
    report = (await client.get(f"/api/corpora/{corpus['id']}/report")).json()
    assert not any(item["key"] == "performance qualification" for item in report["milestones"])
    assert any(item["normalized_value"]["status"] == "amber" for item in report["status"])


@pytest.mark.asyncio
async def test_postgres_checkpoint_resumes_after_application_restart(settings):
    provider_before_restart = FakeLLMProvider()
    app = create_app(settings, provider_before_restart)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as first_client:
            corpus, document_ids = await create_atlas(first_client)
            run = (await first_client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": document_ids})).json()
            assert run["status"] == "waiting_review"
            extraction_calls_before_restart = len([call for call in provider_before_restart.calls if call["method"] == "analyze_document"])

    # A new app, provider, database pool, graph, and checkpointer resume the same thread/run.
    provider_after_restart = FakeLLMProvider()
    restarted_app = create_app(settings, provider_after_restart)
    async with restarted_app.router.lifespan_context(restarted_app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted_app), base_url="http://test") as restarted_client:
            # Simulate replay in the crash window after durable extraction output but before
            # the next checkpoint. Per-document completion markers suppress provider work.
            await restarted_app.state.workflow.analyze({
                "corpus_id": corpus["id"], "run_id": run["id"], "document_ids": document_ids,
            })
            items = (await restarted_client.get(f"/api/runs/{run['id']}/review-items")).json()
            decisions = [{"review_item_id": item["id"], "decision": "approved"} for item in items]
            await resolve_normal_facts(restarted_client, run["id"])
            await restarted_client.post(f"/api/runs/{run['id']}/review", json={"decisions": decisions})
            resumed = await restarted_client.post(f"/api/runs/{run['id']}/resume")
            assert resumed.json()["status"] == "completed"
            report = (await restarted_client.get(f"/api/corpora/{corpus['id']}/report")).json()
            assert any(item["normalized_value"]["status"] == "amber" for item in report["status"])

    assert extraction_calls_before_restart == 8
    assert provider_after_restart.calls == []


@pytest.mark.asyncio
async def test_invalid_grounding_retries_then_escalates(settings):
    provider = UngroundedFakeProvider()
    app = create_app(settings, provider)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            corpus = (await client.post("/api/corpora", json={"name": "Invalid provenance"})).json()
            path = ROOT / "demo_data" / "atlas" / "project_plan.pdf"
            document = (await client.post(f"/api/corpora/{corpus['id']}/documents", files={"file": (path.name, path.read_bytes(), "text/plain")})).json()
            run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [document["id"]]})).json()
            detail = (await client.get(f"/api/runs/{run['id']}")).json()
            stages = [item["stage"] for item in detail["stage_log"]]
            claims = (await client.get(f"/api/runs/{run['id']}/claims")).json()
            items = (await client.get(f"/api/runs/{run['id']}/review-items")).json()
            issue = next(item for item in items if item["item_type"] == "evidence_issue")
            await client.post(f"/api/runs/{run['id']}/review", json={"decisions": [{"review_item_id": issue["id"], "decision": "approved"}]})
            assert (await client.post(f"/api/runs/{run['id']}/resume")).json()["status"] == "completed"
            report = (await client.get(f"/api/corpora/{corpus['id']}/report")).json()

    assert stages.count("analyze") == 2
    assert "retry_invalid" in stages
    assert "escalate_invalid" in stages
    assert all(not claim["grounded"] for claim in claims)
    assert any(item["item_type"] == "evidence_issue" and item["payload"]["message"].startswith("One or more claims failed grounding") for item in items)
    assert all(report[section] == [] for section in ("status", "milestones", "deadlines", "owners", "risks", "decisions", "conflicts", "findings"))
