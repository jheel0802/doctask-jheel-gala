import asyncio
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from project_evidence.config import Settings
from project_evidence.llm import FakeLLMProvider
from project_evidence.main import create_app
from project_evidence.models import CanonicalEntity, Claim, Report

ROOT = Path(__file__).parents[2]
ATLAS_INITIAL = ["project_charter.pdf", "project_plan.pdf", "release_plan.docx", "meeting_notes_01.docx", "meeting_notes_02.docx", "weekly_status_01.txt", "risk_register.pdf", "change_request_01.pdf"]


async def upload_paths(client, corpus_id, paths):
    ids = []
    for path in paths:
        response = await client.post(f"/api/corpora/{corpus_id}/documents", files={"file": (path.name, path.read_bytes(), "text/plain")})
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    return ids


async def approve_and_resume(client, run_id):
    claims = (await client.get(f"/api/runs/{run_id}/claims")).json()
    for claim in claims:
        if claim.get("inclusion_state") == "pending":
            assert (await client.post(f"/api/runs/{run_id}/claims/{claim['id']}/inclusion", json={"included": True})).status_code == 200
    items = (await client.get(f"/api/runs/{run_id}/review-items")).json()
    decisions = [{"review_item_id": item["id"], "decision": "approved"} for item in items]
    assert (await client.post(f"/api/runs/{run_id}/review", json={"decisions": decisions})).status_code == 200
    response = await client.post(f"/api/runs/{run_id}/resume")
    assert response.status_code == 200, response.text
    return response.json(), items


@pytest.fixture
async def phase2_context():
    settings, provider = Settings(), FakeLLMProvider()
    app = create_app(settings, provider)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client, provider, app


@pytest.mark.asyncio
async def test_incremental_update_only_processes_new_document_and_preserves_sections(phase2_context):
    client, provider, _ = phase2_context
    corpus = (await client.post("/api/corpora", json={"name": "Project Atlas incremental"})).json()
    atlas_dir = ROOT / "demo_data" / "atlas"
    initial_paths = [atlas_dir / name for name in ATLAS_INITIAL]
    initial_ids = await upload_paths(client, corpus["id"], initial_paths)
    initial_run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": initial_ids})).json()
    await approve_and_resume(client, initial_run["id"])
    before = (await client.get(f"/api/corpora/{corpus['id']}/report")).json()
    calls_before = len(provider.calls)

    incremental_id = (await upload_paths(client, corpus["id"], [atlas_dir / "change_request_02.docx"]))[0]
    incremental_run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"kind": "incremental", "document_ids": [incremental_id]})).json()
    detail = (await client.get(f"/api/runs/{incremental_run['id']}")).json()
    items = (await client.get(f"/api/runs/{incremental_run['id']}/review-items")).json()

    assert len(provider.calls) - calls_before == 1
    assert set(detail["metrics"]["affected_scope"]) == {
        "decision:customer_pilot_checkpoint", "milestone:beta_release", "risk:vendor_capacity",
        "status:deployment", "milestone:general_availability", "owner:general_availability",
    }
    change_items = [item for item in items if item["item_type"] == "change"]
    assert len(change_items) == 3
    assert {item["payload"]["section"] for item in change_items} == {"milestones", "risks", "decisions"}
    await approve_and_resume(client, incremental_run["id"])
    after = (await client.get(f"/api/corpora/{corpus['id']}/report")).json()

    for section in ("status", "deadlines", "owners", "conflicts"):
        assert after[section] == before[section]
    assert before["findings"] and after["findings"] == []
    assert len(after["decisions"]) == len(before["decisions"]) + 1
    assert any(item["key"] == "customer_pilot_checkpoint" for item in after["decisions"])
    assert next(item for item in after["milestones"] if item["key"] == "beta_release")["normalized_value"]["date"] == "2026-09-04"
    assert next(item for item in after["risks"] if item["key"] == "vendor_capacity")["normalized_value"]["owner"] == "Leah Morgan"


@pytest.mark.asyncio
async def test_rejected_incremental_update_does_not_modify_committed_brief(phase2_context):
    client, _, _ = phase2_context
    corpus = (await client.post("/api/corpora", json={"name": "Rejected Atlas update"})).json()
    atlas_dir = ROOT / "demo_data" / "atlas"
    initial_ids = await upload_paths(client, corpus["id"], [atlas_dir / name for name in ATLAS_INITIAL])
    initial_run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": initial_ids})).json()
    await approve_and_resume(client, initial_run["id"])
    before = (await client.get(f"/api/corpora/{corpus['id']}/report")).json()

    update_id = (await upload_paths(client, corpus["id"], [atlas_dir / "change_request_02.docx"]))[0]
    incremental = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"kind": "incremental", "document_ids": [update_id]})).json()
    items = (await client.get(f"/api/runs/{incremental['id']}/review-items")).json()
    assert len(items) == 3 and all(item["item_type"] == "change" for item in items)
    await client.post(f"/api/runs/{incremental['id']}/review", json={"decisions": [{"review_item_id": item["id"], "decision": "rejected"} for item in items]})
    assert (await client.post(f"/api/runs/{incremental['id']}/resume")).json()["status"] == "completed"
    after = (await client.get(f"/api/corpora/{corpus['id']}/report")).json()
    assert after == before


@pytest.mark.asyncio
async def test_second_clean_corpus_has_zero_conflicts_and_no_state_leak(phase2_context):
    client, _, _ = phase2_context
    corpus = (await client.post("/api/corpora", json={"name": "Project Beacon"})).json()
    paths = sorted((ROOT / "demo_data" / "beacon").glob("*.txt"))
    document_ids = await upload_paths(client, corpus["id"], paths)
    run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": document_ids})).json()
    items = (await client.get(f"/api/runs/{run['id']}/review-items")).json()
    claims = (await client.get(f"/api/runs/{run['id']}/claims")).json()

    assert not any(item["item_type"] == "conflict" for item in items)
    assert {claim["provenance"]["source_document"] for claim in claims} == {path.name for path in paths}
    assert all("Atlas" not in claim["claim"] for claim in claims)


@pytest.mark.asyncio
async def test_two_corpora_run_concurrently_without_mixing(phase2_context):
    client, _, _ = phase2_context
    atlas = (await client.post("/api/corpora", json={"name": "Concurrent Atlas"})).json()
    beacon = (await client.post("/api/corpora", json={"name": "Concurrent Beacon"})).json()
    atlas_paths = [ROOT / "demo_data" / "atlas" / name for name in ATLAS_INITIAL[:3]]
    beacon_paths = sorted((ROOT / "demo_data" / "beacon").glob("*.txt"))
    atlas_ids, beacon_ids = await asyncio.gather(upload_paths(client, atlas["id"], atlas_paths), upload_paths(client, beacon["id"], beacon_paths))

    atlas_response, beacon_response = await asyncio.gather(
        client.post(f"/api/corpora/{atlas['id']}/runs", json={"document_ids": atlas_ids}),
        client.post(f"/api/corpora/{beacon['id']}/runs", json={"document_ids": beacon_ids}),
    )
    assert atlas_response.status_code == beacon_response.status_code == 201
    atlas_run, beacon_run = atlas_response.json(), beacon_response.json()
    atlas_claims, beacon_claims = await asyncio.gather(client.get(f"/api/runs/{atlas_run['id']}/claims"), client.get(f"/api/runs/{beacon_run['id']}/claims"))
    atlas_document_ids = {claim["provenance"]["document_id"] for claim in atlas_claims.json()}
    beacon_document_ids = {claim["provenance"]["document_id"] for claim in beacon_claims.json()}
    assert atlas_document_ids.isdisjoint(beacon_document_ids)
    assert atlas_document_ids == set(atlas_ids)
    assert beacon_document_ids == set(beacon_ids)


@pytest.mark.asyncio
async def test_concurrent_runs_same_corpus_remain_isolated(phase2_context):
    client, _, app = phase2_context
    corpus = (await client.post("/api/corpora", json={"name": "Concurrent same corpus"})).json()
    path = ROOT / "demo_data" / "beacon" / "status_report.txt"
    document_id = (await upload_paths(client, corpus["id"], [path]))[0]

    first_response, second_response = await asyncio.gather(
        client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [document_id]}),
        client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [document_id]}),
    )

    assert first_response.status_code == second_response.status_code == 201
    first, second = first_response.json(), second_response.json()
    assert first["id"] != second["id"]
    assert first["corpus_id"] == second["corpus_id"] == corpus["id"]
    assert first["status"] == second["status"] == "waiting_review"

    first_claims, second_claims = await asyncio.gather(
        client.get(f"/api/runs/{first['id']}/claims"),
        client.get(f"/api/runs/{second['id']}/claims"),
    )
    assert first_claims.status_code == second_claims.status_code == 200
    first_claims, second_claims = first_claims.json(), second_claims.json()
    assert first_claims and second_claims
    assert {claim["id"] for claim in first_claims}.isdisjoint({claim["id"] for claim in second_claims})
    assert {(claim["type"], claim["entity_key"], json.dumps(claim["normalized_value"], sort_keys=True)) for claim in first_claims} == {
        (claim["type"], claim["entity_key"], json.dumps(claim["normalized_value"], sort_keys=True)) for claim in second_claims
    }
    async with app.state.database.sessions() as session:
        stored_first = (await session.scalars(select(Claim).where(Claim.run_id == first["id"]))).all()
        stored_second = (await session.scalars(select(Claim).where(Claim.run_id == second["id"]))).all()
        assert {str(claim.id) for claim in stored_first} == {claim["id"] for claim in first_claims}
        assert {str(claim.id) for claim in stored_second} == {claim["id"] for claim in second_claims}

    checkpointer = app.state.workflow.graph.checkpointer
    first_checkpoint, second_checkpoint = await asyncio.gather(
        checkpointer.aget_tuple({"configurable": {"thread_id": first["id"]}}),
        checkpointer.aget_tuple({"configurable": {"thread_id": second["id"]}}),
    )
    assert first_checkpoint is not None and second_checkpoint is not None
    assert first_checkpoint.config["configurable"]["thread_id"] == first["id"]
    assert second_checkpoint.config["configurable"]["thread_id"] == second["id"]
    assert first_checkpoint.config["configurable"]["checkpoint_id"] != second_checkpoint.config["configurable"]["checkpoint_id"]

    first_result, _ = await approve_and_resume(client, first["id"])
    second_result, _ = await approve_and_resume(client, second["id"])
    assert first_result["status"] == "completed"
    assert second_result["status"] == "failed"
    second_detail = (await client.get(f"/api/runs/{second['id']}")).json()
    assert second_detail["error"]["retry_possible"] is False
    assert "Report changed" in second_detail["error"]["message"]

    runs = (await client.get(f"/api/corpora/{corpus['id']}/runs")).json()
    assert {run["id"] for run in runs} == {first["id"], second["id"]}
    report = (await client.get(f"/api/corpora/{corpus['id']}/report")).json()
    assert report["status"]
    assert all(item["run_id"] == first["id"] for item in report["recent_changes"])
    async with app.state.database.sessions() as session:
        stored_report = await session.get(Report, corpus["id"])
        entities = (await session.scalars(select(CanonicalEntity).where(CanonicalEntity.corpus_id == corpus["id"]))).all()
        assert stored_report.version == 1
        assert len({entity.entity_id for entity in entities}) == len(entities)


@pytest.mark.asyncio
async def test_budget_limit_fails_gracefully_and_preserves_completed_work():
    settings, provider = Settings(max_llm_calls_per_run=1), FakeLLMProvider()
    app = create_app(settings, provider)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            corpus = (await client.post("/api/corpora", json={"name": "Budget guard"})).json()
            paths = sorted((ROOT / "demo_data" / "beacon").glob("*.txt"))
            ids = await upload_paths(client, corpus["id"], paths)
            run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": ids})).json()
            detail = (await client.get(f"/api/runs/{run['id']}")).json()

    assert detail["status"] == "failed"
    assert detail["error"]["retry_possible"] is True
    assert "limit" in detail["error"]["message"]
    assert detail["metrics"]["llm_calls"] == 1
    assert len(detail["metrics"]["completed_llm"]["analyze"]) == 1
    assert any(stage["stage"] == "fail_gracefully" and stage["status"] == "failed" for stage in detail["stage_log"])
    assert all("latency_ms" in stage and "error" in stage for stage in detail["stage_log"])


class FailOnceProvider(FakeLLMProvider):
    failed = False

    async def analyze_document(self, **kwargs):
        if not self.failed:
            self.failed = True
            raise RuntimeError("synthetic provider outage")
        return await super().analyze_document(**kwargs)


@pytest.mark.asyncio
async def test_provider_failure_is_resumable_without_losing_classification():
    settings, provider = Settings(), FailOnceProvider()
    app = create_app(settings, provider)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            corpus = (await client.post("/api/corpora", json={"name": "Provider recovery"})).json()
            path = ROOT / "demo_data" / "beacon" / "status_report.txt"
            document_id = (await upload_paths(client, corpus["id"], [path]))[0]
            run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [document_id]})).json()
            failed = (await client.get(f"/api/runs/{run['id']}")).json()
            assert failed["status"] == "failed"
            assert failed["metrics"]["completed_llm"]["analyze"] == []
            resumed = await client.post(f"/api/runs/{run['id']}/resume")
            assert resumed.json()["status"] == "waiting_review"
            recovered = (await client.get(f"/api/runs/{run['id']}")).json()
            assert recovered["metrics"]["llm_calls"] == 1
            assert sum(call["method"] == "analyze_document" for call in provider.calls) == 1


@pytest.mark.asyncio
async def test_same_corpus_stale_run_cannot_overwrite_newer_report(phase2_context):
    client, _, _ = phase2_context
    corpus = (await client.post("/api/corpora", json={"name": "Stale write"})).json()
    path = ROOT / "demo_data" / "beacon" / "status_report.txt"
    document_id = (await upload_paths(client, corpus["id"], [path]))[0]
    first = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [document_id]})).json()
    second = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [document_id]})).json()
    assert (await approve_and_resume(client, first["id"]))[0]["status"] == "completed"
    second_result, _ = await approve_and_resume(client, second["id"])
    assert second_result["status"] == "failed"
    detail = (await client.get(f"/api/runs/{second['id']}")).json()
    assert detail["error"]["retry_possible"] is False
    assert "Report changed" in detail["error"]["message"]


@pytest.mark.asyncio
async def test_incremental_run_rejects_already_processed_duplicate(phase2_context):
    client, provider, _ = phase2_context
    corpus = (await client.post("/api/corpora", json={"name": "Duplicate processing guard"})).json()
    path = ROOT / "demo_data" / "beacon" / "status_report.txt"
    document_id = (await upload_paths(client, corpus["id"], [path]))[0]
    initial = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [document_id]})).json()
    assert initial["status"] == "waiting_review"
    calls_before = len(provider.calls)
    duplicate = await client.post(f"/api/corpora/{corpus['id']}/runs", json={"kind": "incremental", "document_ids": [document_id]})
    assert duplicate.status_code == 400
    assert len(provider.calls) == calls_before


@pytest.mark.asyncio
async def test_overdue_rule_can_be_disabled():
    settings = Settings()
    app = create_app(settings, FakeLLMProvider())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            corpus = (await client.post("/api/corpora", json={"name": "Configured rules"})).json()
            ruleset = (await client.get(f"/api/corpora/{corpus['id']}/ruleset")).json()
            for rule in ruleset["rules"]:
                if rule["id"] == "overdue_milestone": rule["enabled"] = False
            assert (await client.put(f"/api/corpora/{corpus['id']}/ruleset", json={"ruleset": ruleset})).status_code == 200
            content = b"The old gate remains incomplete and was due on January 1, 2000.\n"
            document = (await client.post(f"/api/corpora/{corpus['id']}/documents", files={"file": ("old.txt", content, "text/plain")})).json()
            run = (await client.post(f"/api/corpora/{corpus['id']}/runs", json={"document_ids": [document["id"]]})).json()
            items = (await client.get(f"/api/runs/{run['id']}/review-items")).json()
    assert not any(item["item_type"] == "finding" and item["payload"].get("message") == "Milestone is overdue and incomplete." for item in items)
