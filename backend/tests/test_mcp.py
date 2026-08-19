import base64
from pathlib import Path

import pytest
from mcp import Client

from project_evidence.config import Settings
from project_evidence.llm import FakeLLMProvider
from project_evidence.mcp_server import create_mcp_server


ROOT = Path(__file__).parents[2]


async def call(client: Client, name: str, arguments: dict | None = None):
    result = await client.call_tool(name, arguments or {})
    assert result.is_error is False, result.content
    structured = result.structured_content
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured


def encoded(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


@pytest.mark.asyncio
async def test_mcp_end_to_end_uses_shared_workflow_and_preserves_isolation():
    server = create_mcp_server(Settings(), FakeLLMProvider())
    async with Client(server, raise_exceptions=True) as client:
        listed = await client.list_tools()
        names = {tool.name for tool in listed.tools}
        assert {
            "create_corpus", "list_corpora", "list_documents", "add_document",
            "start_analysis", "get_analysis", "get_extracted_information",
            "set_draft_fact_inclusion", "get_draft_brief", "get_review_items",
            "submit_review_decisions", "resume_analysis", "get_committed_brief",
        } <= names

        atlas = await call(client, "create_corpus", {"name": "MCP Atlas"})
        beacon = await call(client, "create_corpus", {"name": "MCP Beacon"})
        corpora = await call(client, "list_corpora")
        assert {atlas["id"], beacon["id"]} <= {item["id"] for item in corpora}

        atlas_dir = ROOT / "demo_data" / "atlas"
        atlas_ids = []
        for filename in ("project_plan.pdf", "meeting_notes_01.docx", "weekly_status_01.txt"):
            item = await call(client, "add_document", {
                "corpus_id": atlas["id"], "filename": filename,
                "content_base64": encoded(atlas_dir / filename), "media_type": "text/plain",
            })
            atlas_ids.append(item["id"])

        initial = await call(client, "start_analysis", {"corpus_id": atlas["id"], "document_ids": atlas_ids, "kind": "initial"})
        assert initial["status"] == "waiting_review"
        analysis = await call(client, "get_analysis", {"run_id": initial["id"]})
        assert analysis["metrics"]["run_kind"] == "initial"
        claims = await call(client, "get_extracted_information", {"run_id": initial["id"]})
        assert len(claims) >= 9 and all(claim["grounded"] for claim in claims)
        draft = await call(client, "get_draft_brief", {"run_id": initial["id"]})
        assert draft["status"] == "draft"
        assert {item["normalized_value"]["status"] for item in draft["data"]["status"]} == {"amber", "delayed"}
        assert (await call(client, "get_committed_brief", {"corpus_id": atlas["id"]}))["status"] == []
        review = await call(client, "get_review_items", {"run_id": initial["id"]})
        assert sorted(item["item_type"] for item in review) == ["conflict", "finding"]
        for claim in claims:
            if claim.get("inclusion_state") == "pending":
                await call(client, "set_draft_fact_inclusion", {"run_id": initial["id"], "claim_id": claim["id"], "included": True})
        conflict = next(item for item in review if item["item_type"] == "conflict")
        selected = conflict["payload"]["claims"][0]
        decisions = []
        for item in review:
            decision = {"review_item_id": item["id"], "decision": "approved"}
            if item["item_type"] == "conflict":
                decision.update({"conflict_action": "select_claim", "selected_claim_id": selected["claim_id"]})
            decisions.append(decision)
        await call(client, "submit_review_decisions", {
            "run_id": initial["id"],
            "decisions": decisions,
        })
        assert (await call(client, "resume_analysis", {"run_id": initial["id"]}))["status"] == "completed"
        atlas_report = await call(client, "get_committed_brief", {"corpus_id": atlas["id"]})
        assert {item["normalized_value"]["status"] for item in atlas_report["status"]} == {"amber", "delayed"}
        assert atlas_report["conflicts"] == [] and len(atlas_report["findings"]) == 1
        assert atlas_report["deadlines"][0]["human_resolution"]["selected_claim_id"] == selected["claim_id"]
        resolution_audit = next(item for item in atlas_report["recent_changes"] if item.get("human_resolution"))
        assert len(resolution_audit["source_documents"]) >= 2

        beacon_dir = ROOT / "demo_data" / "beacon"
        beacon_ids = []
        for filename in ("project_plan.txt", "status_report.txt"):
            item = await call(client, "add_document", {
                "corpus_id": beacon["id"], "filename": filename,
                "content_base64": encoded(beacon_dir / filename), "media_type": "text/plain",
            })
            beacon_ids.append(item["id"])
        beacon_run = await call(client, "start_analysis", {"corpus_id": beacon["id"], "document_ids": beacon_ids, "kind": "initial"})
        beacon_claims = await call(client, "get_extracted_information", {"run_id": beacon_run["id"]})
        assert {claim["provenance"]["document_id"] for claim in claims}.isdisjoint({claim["provenance"]["document_id"] for claim in beacon_claims})
        assert not await call(client, "get_review_items", {"run_id": beacon_run["id"]})
        for claim in beacon_claims:
            if claim.get("inclusion_state") == "pending":
                await call(client, "set_draft_fact_inclusion", {"run_id": beacon_run["id"], "claim_id": claim["id"], "included": True})
        await call(client, "submit_review_decisions", {"run_id": beacon_run["id"], "decisions": []})
        await call(client, "resume_analysis", {"run_id": beacon_run["id"]})
        beacon_report = await call(client, "get_committed_brief", {"corpus_id": beacon["id"]})
        assert beacon_report["status"][0]["normalized_value"]["status"] == "green"
        assert any(item["normalized_value"]["status"] == "amber" for item in atlas_report["status"])

        update = await call(client, "add_document", {
            "corpus_id": atlas["id"], "filename": "change_request_02.docx",
            "content_base64": encoded(atlas_dir / "change_request_02.docx"), "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        })
        incremental = update["incremental_run"]
        updates = await call(client, "get_review_items", {"run_id": incremental["id"]})
        assert len(updates) == 3 and all(item["item_type"] == "change" for item in updates)
        await call(client, "submit_review_decisions", {
            "run_id": incremental["id"],
            "decisions": [{"review_item_id": updates[0]["id"], "decision": "rejected"}],
        })
        await call(client, "resume_analysis", {"run_id": incremental["id"]})
        assert await call(client, "get_committed_brief", {"corpus_id": atlas["id"]}) == atlas_report
