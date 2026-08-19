import hashlib
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, Field

from .atlas_ground_truth import DOCUMENT_TYPES, EXPECTED_CLAIMS

T = TypeVar("T")


class ExtractedClaim(BaseModel):
    type: str
    entity_key: str
    claim: str
    normalized_value: dict[str, Any]
    span_index: int
    evidence: str
    confidence: float = Field(ge=0, le=1)
    relationship: str = "new_entity"
    canonical_entity_id: str | None = None
    changed_fields: dict[str, Any] = Field(default_factory=dict)


class DocumentAnalysis(BaseModel):
    document_type: str
    claims: list[ExtractedClaim]


class LLMResult(BaseModel, Generic[T]):
    value: T
    calls: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class LLMProvider(Protocol):
    async def analyze_document(self, *, filename: str, untrusted_spans: list[dict[str, Any]], canonical_context: list[dict[str, Any]] | None = None) -> LLMResult[DocumentAnalysis]: ...


@dataclass
class FakeLLMProvider:
    """Deterministic fingerprint-to-structure adapter for known fixtures; no network I/O."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    name = "fake"
    fixture_facts = {
        "Beta release is planned for August 28, 2026, and remains in planned status.": ("milestone", "beta_release", {"date": "2026-08-28", "status": "planned"}),
        "The production launch target in the approved plan is September 12, 2026.": ("deadline", "production_launch", {"date": "2026-09-12"}),
        "A revised planning draft lists the production launch target as September 19, 2026.": ("deadline", "production_launch", {"date": "2026-09-19"}),
        "Maya Chen owns delivery of the beta release.": ("owner", "beta_release", {"owner": "Maya Chen"}),
        "The team agreed that the production rollout date should move to September 26, 2026.": ("deadline", "production_rollout_date", {"date": "2026-09-26"}),
        "The launch will use a phased rollout.": ("decision", "launch_rollout", {"decision": "phased rollout"}),
        "Project Atlas is amber because deployment is delayed.": ("status", "project", {"status": "amber"}),
        "Vendor capacity remains an open, high-severity delivery risk and no owner has been assigned.": ("risk", "vendor_capacity", {"severity": "high", "owner": None, "status": "open"}),
        "The team adopted a two-reviewer QA exit gate.": ("decision", "qa_exit", {"decision": "two-reviewer QA exit gate"}),
        "The field pilot is planned for November 3, 2026, and remains in planned status.": ("milestone", "field_pilot", {"date": "2026-11-03", "status": "planned"}),
        "The customer preview is scheduled for November 18, 2026.": ("deadline", "customer_preview", {"date": "2026-11-18"}),
        "Elena Ruiz owns the field pilot.": ("owner", "field_pilot", {"owner": "Elena Ruiz"}),
        "Project Beacon is green.": ("status", "project", {"status": "green"}),
        "Device shipping is an open medium-severity risk owned by Omar Patel.": ("risk", "device_shipping", {"severity": "medium", "owner": "Omar Patel", "status": "open"}),
        "The old gate remains incomplete and was due on January 1, 2000.": ("milestone", "old_gate", {"date": "2000-01-01", "status": "planned"}),
        "The approved beta release date has changed to September 4, 2026, and remains planned.": ("milestone", "beta_release", {"date": "2026-09-04", "status": "planned"}),
        "Jordan Lee is the executive sponsor for Project Atlas.": ("owner", "executive_sponsor", {"owner": "Jordan Lee"}),
        "General availability readiness is planned for September 5, 2026.": ("milestone", "general_availability", {"date": "2026-09-05", "status": "planned"}),
        "Priya Shah owns general availability readiness.": ("owner", "general_availability", {"owner": "Priya Shah"}),
        "The security review was completed on August 20, 2026.": ("milestone", "security_review", {"date": "2026-08-20", "status": "completed"}),
        "Beta release remains scheduled for August 28, 2026.": ("milestone", "beta_release", {"date": "2026-08-28", "status": "planned"}),
        "The release plan retains September 12, 2026, as the production launch date.": ("deadline", "production_launch_date", {"date": "2026-09-12"}),
        "The group confirmed that Maya Chen owns the beta release.": ("owner", "beta_release", {"owner": "Maya Chen"}),
        "The beta release remains planned for August 28, 2026.": ("milestone", "beta_release", {"date": "2026-08-28", "status": "planned"}),
        "Performance qualification completed on August 22, 2026.": ("milestone", "performance_qualification", {"date": "2026-08-22", "status": "completed"}),
        "Deployment remains delayed.": ("status", "deployment", {"status": "delayed"}),
        "QA automation is a closed medium-severity risk owned by Elena Ruiz.": ("risk", "qa_automation", {"severity": "medium", "owner": "Elena Ruiz", "status": "closed"}),
        "Data migration is an open medium-severity risk owned by Omar Patel.": ("risk", "data_migration", {"severity": "medium", "owner": "Omar Patel", "status": "open"}),
        "API stability is a mitigated low-severity risk owned by Priya Shah.": ("risk", "api_stability", {"severity": "low", "owner": "Priya Shah", "status": "mitigated"}),
        "The approved scope adds audit-log export as a launch requirement.": ("decision", "audit_log_export", {"decision": "add audit-log export as a launch requirement"}),
        "Maya Chen owns delivery of the audit-log export requirement.": ("owner", "audit_log_export", {"owner": "Maya Chen"}),
        "Leah Morgan now owns the vendor capacity risk, which remains high severity and is mitigating.": ("risk", "vendor_capacity", {"severity": "high", "owner": "Leah Morgan", "status": "mitigating"}),
        "The team approved a customer pilot checkpoint before broad production rollout.": ("decision", "customer_pilot_checkpoint", {"decision": "customer pilot checkpoint before broad rollout"}),
    }
    fixture_by_fingerprint = {
        hashlib.sha256(text.strip().encode()).hexdigest(): [(text, *data)]
        for text, data in fixture_facts.items()
    }
    for fixture_entries in EXPECTED_CLAIMS.values():
        for fixture_entry in fixture_entries:
            converted_fixtures = []
            for fixture_claim in fixture_entry["claims"]:
                converted_fixtures.append((fixture_entry["evidence"], *fixture_claim))
            fixture_by_fingerprint[hashlib.sha256(fixture_entry["evidence"].encode()).hexdigest()] = converted_fixtures

    async def analyze_document(self, *, filename: str, untrusted_spans: list[dict[str, Any]], canonical_context: list[dict[str, Any]] | None = None) -> LLMResult[DocumentAnalysis]:
        context = canonical_context or []
        self.calls.append({"method": "analyze_document", "filename": filename, "canonical_context": context})
        lowered = filename.lower()
        kind = DOCUMENT_TYPES.get(filename, next((name for name in ("project_charter", "release_plan", "meeting_notes", "project_plan", "weekly_status", "risk_register", "change_request") if name in lowered), "unknown"))
        claims = []
        for span in untrusted_spans:
            fingerprint = hashlib.sha256(span["text"].strip().encode()).hexdigest()
            fixtures = self.fixture_by_fingerprint.get(fingerprint)
            if not fixtures:
                continue
            for claim_text, fact_type, entity_key, normalized in fixtures:
                candidate_ids = {item["entity_id"] for item in context}
                target = entity_key if entity_key in candidate_ids else None
                # The deterministic fixture oracle supplies the one genuinely
                # semantic Atlas identity; production logic has no such alias.
                if not target and filename == "meeting_notes_01.docx" and entity_key == "production_rollout_date" and "production_launch" in candidate_ids:
                    target = "production_launch"
                existing = next((item for item in context if item["entity_id"] == target), None)
                relationship = "new_entity"
                changed = {}
                if existing:
                    shared = set(normalized) & set(existing.get("fields", {}))
                    changed = {key: {"existing": existing["fields"][key], "candidate": normalized[key]} for key in shared if existing["fields"][key] != normalized[key]}
                    relationship = "conflicts_with_existing" if changed else ("enriches_existing" if set(normalized) - set(existing.get("fields", {})) else "supports_existing")
                claims.append(ExtractedClaim(type=fact_type, entity_key=entity_key, claim=claim_text, normalized_value=normalized, span_index=span["index"], evidence=span["text"], confidence=0.95, relationship=relationship, canonical_entity_id=target, changed_fields=changed))
        return LLMResult(value=DocumentAnalysis(document_type=kind, claims=claims), input_tokens=sum(len(s["text"].split()) for s in untrusted_spans), output_tokens=2 + len(claims) * 20)
