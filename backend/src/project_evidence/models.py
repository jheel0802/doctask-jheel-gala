import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from .rules import default_ruleset


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


class Corpus(Base):
    __tablename__ = "corpora"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200))
    ruleset: Mapped[dict[str, Any]] = mapped_column(JSONB, default=default_ruleset)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("corpus_id", "sha256"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    corpus_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    media_type: Mapped[str] = mapped_column(String(120))
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    spans: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    parse_status: Mapped[str] = mapped_column(String(30), default="pending")
    classification: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    corpus_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(40), default="created")
    document_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    stage_log: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (UniqueConstraint("run_id", "document_id", "fingerprint"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    corpus_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(30))
    entity_key: Mapped[str] = mapped_column(String(200))
    claim: Mapped[str] = mapped_column(Text)
    normalized_value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    confidence: Mapped[float]
    grounded: Mapped[bool] = mapped_column(default=False)
    fingerprint: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(64), nullable=True)
    canonical_entity_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    relationship: Mapped[str | None] = mapped_column(String(40), nullable=True)


class CanonicalEntity(Base):
    __tablename__ = "canonical_entities"
    __table_args__ = (UniqueConstraint("corpus_id", "entity_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    corpus_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[str] = mapped_column(String(200))
    entity_type: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(240))
    aliases: Mapped[list[str]] = mapped_column(JSONB, default=list)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    claim_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    provenance: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Conflict(Base):
    __tablename__ = "conflicts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    corpus_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    entity_key: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)


class RuleFinding(Base):
    __tablename__ = "rule_findings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    corpus_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[str] = mapped_column(String(100))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ProposedChange(Base):
    __tablename__ = "proposed_changes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    corpus_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    section: Mapped[str] = mapped_column(String(40))
    operation: Mapped[str] = mapped_column(String(20), default="upsert")
    item_key: Mapped[str] = mapped_column(String(200))
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    provenance: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (UniqueConstraint("run_id", "item_type", "target_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    corpus_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    item_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    decision: Mapped[str] = mapped_column(String(20), default="pending")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Report(Base):
    __tablename__ = "reports"
    corpus_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
