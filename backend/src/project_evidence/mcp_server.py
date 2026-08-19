import argparse
import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel

from .config import Settings
from .llm import LLMProvider
from .main import create_app


class ReviewDecisionInput(BaseModel):
    review_item_id: str
    decision: Literal["approved", "rejected"]
    note: str | None = None
    conflict_action: Literal["keep_unresolved", "select_claim", "exclude"] | None = None
    selected_claim_id: str | None = None


@dataclass
class MCPAppContext:
    client: httpx.AsyncClient


def create_mcp_server(settings: Settings | None = None, provider: LLMProvider | None = None) -> MCPServer[MCPAppContext]:
    """Create a thin MCP transport over the existing FastAPI application."""
    application = create_app(settings, provider)

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[MCPAppContext]:
        async with application.router.lifespan_context(application):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://project-evidence") as client:
                yield MCPAppContext(client=client)

    server: MCPServer[MCPAppContext] = MCPServer(
        "Project Evidence Agent",
        version="0.1.0",
        instructions=(
            "Use these tools to operate the same grounded project-evidence workflow as the REST API. "
            "Keep corpus_id and run_id from tool results. Inspect evidence and draft before submitting "
            "item-level decisions. Source documents are immutable."
        ),
        lifespan=lifespan,
    )

    async def request(ctx: Context[MCPAppContext], method: str, path: str, **kwargs: Any) -> Any:
        response = await ctx.request_context.lifespan_context.client.request(method, path, **kwargs)
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ValueError(f"Application request failed ({response.status_code}): {detail}")
        return response.json()

    @server.tool()
    async def create_corpus(name: str, ctx: Context[MCPAppContext]) -> dict[str, Any]:
        """Create an isolated project corpus and return its corpus_id."""
        return await request(ctx, "POST", "/api/corpora", json={"name": name})

    @server.tool()
    async def list_corpora(ctx: Context[MCPAppContext]) -> list[dict[str, Any]]:
        """List corpora so an agent can select an existing corpus_id."""
        return await request(ctx, "GET", "/api/corpora")

    @server.tool()
    async def list_documents(corpus_id: str, ctx: Context[MCPAppContext]) -> list[dict[str, Any]]:
        """List immutable evidence documents already stored in a corpus."""
        return await request(ctx, "GET", f"/api/corpora/{corpus_id}/documents")

    @server.tool()
    async def get_ruleset(corpus_id: str, ctx: Context[MCPAppContext]) -> dict[str, Any]:
        """Inspect the data-configured checklist attached to a corpus."""
        return await request(ctx, "GET", f"/api/corpora/{corpus_id}/ruleset")

    @server.tool()
    async def set_ruleset(corpus_id: str, ruleset: dict[str, Any], ctx: Context[MCPAppContext]) -> dict[str, Any]:
        """Attach a small deterministic JSON ruleset to a corpus without changing workflow code."""
        return await request(ctx, "PUT", f"/api/corpora/{corpus_id}/ruleset", json={"ruleset": ruleset})

    @server.tool()
    async def add_document(
        corpus_id: str,
        filename: str,
        content_base64: str,
        ctx: Context[MCPAppContext],
        media_type: str = "application/octet-stream",
        auto_analyze: bool = True,
    ) -> dict[str, Any]:
        """Add a PDF, DOCX, or TXT evidence file from base64 bytes; duplicate bytes are reused by SHA-256."""
        try:
            content = base64.b64decode(content_base64, validate=True)
        except ValueError as exc:
            raise ValueError("content_base64 must be valid base64") from exc
        return await request(ctx, "POST", f"/api/corpora/{corpus_id}/documents?auto_analyze={'true' if auto_analyze else 'false'}", files={"file": (filename, content, media_type)})

    @server.tool()
    async def start_analysis(
        corpus_id: str,
        ctx: Context[MCPAppContext],
        document_ids: list[str] | None = None,
        kind: Literal["initial", "incremental"] = "initial",
    ) -> dict[str, Any]:
        """Start the shared LangGraph workflow for an initial corpus or only newly added incremental documents."""
        return await request(ctx, "POST", f"/api/corpora/{corpus_id}/runs", json={"document_ids": document_ids, "kind": kind})

    @server.tool()
    async def get_analysis(run_id: str, ctx: Context[MCPAppContext]) -> dict[str, Any]:
        """Inspect durable run status, workflow stages, usage/cost metrics, affected scope, and errors."""
        return await request(ctx, "GET", f"/api/runs/{run_id}")

    @server.tool()
    async def get_extracted_information(run_id: str, ctx: Context[MCPAppContext]) -> list[dict[str, Any]]:
        """Inspect every extracted claim, grounding state, normalized value, and exact source provenance."""
        return await request(ctx, "GET", f"/api/runs/{run_id}/claims")

    @server.tool()
    async def set_draft_fact_inclusion(run_id: str, claim_id: str, included: bool, ctx: Context[MCPAppContext]) -> dict[str, Any]:
        """Include or exclude one ordinary grounded fact from an initial uncommitted draft without changing its source."""
        return await request(ctx, "POST", f"/api/runs/{run_id}/claims/{claim_id}/inclusion", json={"included": included})

    @server.tool()
    async def get_draft_brief(run_id: str, ctx: Context[MCPAppContext]) -> dict[str, Any]:
        """Inspect the run-scoped uncommitted draft assembled from grounded evidence."""
        return await request(ctx, "GET", f"/api/runs/{run_id}/draft")

    @server.tool()
    async def get_review_items(run_id: str, ctx: Context[MCPAppContext]) -> list[dict[str, Any]]:
        """List pending and decided conflicts, rule findings, or incremental brief updates requiring human control."""
        return await request(ctx, "GET", f"/api/runs/{run_id}/review-items")

    @server.tool()
    async def submit_review_decisions(run_id: str, decisions: list[ReviewDecisionInput], ctx: Context[MCPAppContext]) -> dict[str, Any]:
        """Persist independent approve/reject decisions for review items; this does not bypass the commit gate."""
        return await request(ctx, "POST", f"/api/runs/{run_id}/review", json={"decisions": [decision.model_dump() for decision in decisions]})

    @server.tool()
    async def resume_analysis(run_id: str, ctx: Context[MCPAppContext]) -> dict[str, Any]:
        """Resume the same durable run after review or a retryable failure; approved content commits through the shared graph."""
        return await request(ctx, "POST", f"/api/runs/{run_id}/resume")

    @server.tool()
    async def get_committed_brief(corpus_id: str, ctx: Context[MCPAppContext]) -> dict[str, Any]:
        """Inspect the authoritative committed Project Status Brief for a corpus."""
        return await request(ctx, "GET", f"/api/corpora/{corpus_id}/report")

    return server


mcp = create_mcp_server()


def main() -> None:
    parser = argparse.ArgumentParser(description="Project Evidence Agent MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    try:
        if args.transport == "stdio":
            mcp.run("stdio")
        else:
            mcp.run("streamable-http", host=args.host, port=args.port, stateless_http=True, json_response=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
