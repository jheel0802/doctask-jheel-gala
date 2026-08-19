"""Deterministic Atlas oracle kept separate from the human-facing source documents.

The fixture generator and FakeLLMProvider share this manifest. Source files contain
ordinary workplace prose only; they never contain test markers or serialized facts.
"""

DOCUMENT_TYPES = {
    "project_charter.pdf": "project_charter",
    "project_plan.pdf": "project_plan",
    "release_plan.docx": "release_plan",
    "meeting_notes_01.docx": "meeting_notes",
    "meeting_notes_02.docx": "meeting_notes",
    "weekly_status_01.txt": "weekly_status",
    "risk_register.pdf": "risk_register",
    "change_request_01.pdf": "change_request",
    "change_request_02.docx": "change_request",
}

# Each evidence string is one parser span and may support several claims.
EXPECTED_CLAIMS = {
    "project_charter.pdf": [
        {"evidence": "Executive sponsorship sits with Jordan Lee, who will chair the monthly steering review and arbitrate cross-functional scope questions.", "claims": [
            ("owner", "executive_sponsor", {"owner": "Jordan Lee"})]},
    ],
    "project_plan.pdf": [
        {"evidence": "The beta release remains planned for August 28, 2026. Maya Chen is accountable for coordinating the release package, acceptance evidence, and go/no-go readout.", "claims": [
            ("milestone", "beta_release", {"date": "2026-08-28", "status": "planned"}),
            ("owner", "beta_release", {"owner": "Maya Chen"})]},
        {"evidence": "The approved baseline targets production launch for September 12, 2026, following a five-business-day stabilization window.", "claims": [
            ("deadline", "production_launch", {"date": "2026-09-12"})]},
        {"evidence": "General-availability readiness is planned for September 5, 2026, with Priya Shah responsible for the readiness package and stakeholder sign-off.", "claims": [
            ("milestone", "general_availability", {"date": "2026-09-05", "status": "planned"}),
            ("owner", "general_availability", {"owner": "Priya Shah"})]},
        {"evidence": "The security review completed on August 20, 2026; the signed assessment is stored with the release evidence.", "claims": [
            ("milestone", "security_review", {"date": "2026-08-20", "status": "completed"})]},
        {"evidence": "Integration rehearsal is planned for September 2, 2026, and Omar Patel owns execution across platform and data teams.", "claims": [
            ("milestone", "integration_rehearsal", {"date": "2026-09-02", "status": "planned"}),
            ("owner", "integration_rehearsal", {"owner": "Omar Patel"})]},
    ],
    "release_plan.docx": [
        {"evidence": "The release train still places beta on August 28, 2026, with the package held in planned status until exit evidence is accepted.", "claims": [
            ("milestone", "beta_release", {"date": "2026-08-28", "status": "planned"})]},
        {"evidence": "The launch will use a phased rollout: internal users first, then two customer cohorts, then broad availability after health checks.", "claims": [
            ("decision", "launch_rollout", {"decision": "phased rollout"})]},
        {"evidence": "For production, the release baseline continues to show September 12, 2026 as the launch date.", "claims": [
            ("deadline", "production_launch_date", {"date": "2026-09-12"})]},
    ],
    "meeting_notes_01.docx": [
        {"evidence": "Decision: after reviewing the partner lead time and the holiday support roster, the group moved the production rollout date to September 26, 2026. The decision was accepted by product, engineering, and operations.", "claims": [
            ("deadline", "production_rollout_date", {"date": "2026-09-26"})]},
        {"evidence": "Decision: the QA exit gate will require two independent reviewers before release evidence can be marked complete.", "claims": [
            ("decision", "qa_exit", {"decision": "two-reviewer QA exit gate"})]},
    ],
    "meeting_notes_02.docx": [
        {"evidence": "Maya Chen confirmed that she remains the accountable owner for the beta release and will circulate the final checklist.", "claims": [
            ("owner", "beta_release", {"owner": "Maya Chen"})]},
        {"evidence": "The beta release is still planned for August 28, 2026; no change was approved during this checkpoint.", "claims": [
            ("milestone", "beta_release", {"date": "2026-08-28", "status": "planned"})]},
        {"evidence": "Performance qualification completed on August 22, 2026, after the final load-test rerun passed.", "claims": [
            ("milestone", "performance_qualification", {"date": "2026-08-22", "status": "completed"})]},
    ],
    "weekly_status_01.txt": [
        {"evidence": "Overall project health is Amber this week. The primary reason is slippage in deployment preparation rather than a change to the product scope.", "claims": [
            ("status", "project_atlas", {"status": "amber", "reason": "deployment preparation slippage"})]},
        {"evidence": "Deployment preparation remains delayed while the operations team works through environment certification.", "claims": [
            ("status", "deployment", {"status": "delayed"})]},
        {"evidence": "Vendor capacity remains an open, High-severity delivery risk. Ownership is still unassigned after this week's escalation.", "claims": [
            ("risk", "vendor_capacity", {"severity": "high", "owner": None, "status": "open"})]},
        {"evidence": "QA automation is a Medium-severity risk that is now closed; Elena Ruiz owned the remediation through completion.", "claims": [
            ("risk", "qa_automation", {"severity": "medium", "owner": "Elena Ruiz", "status": "closed"})]},
    ],
    "risk_register.pdf": [
        {"evidence": "R-014 | Vendor capacity | High | Open | Unassigned | Escalate staffing commitment at the next supplier review.", "claims": [
            ("risk", "vendor_capacity", {"severity": "high", "owner": None, "status": "open"})]},
        {"evidence": "R-021 | Data migration | Medium | Open | Omar Patel | Rehearse rollback and validate reconciliation totals.", "claims": [
            ("risk", "data_migration", {"severity": "medium", "owner": "Omar Patel", "status": "open"})]},
        {"evidence": "R-026 | API stability | Low | Mitigated | Priya Shah | Rate-limit tests passed; continue production telemetry review.", "claims": [
            ("risk", "api_stability", {"severity": "low", "owner": "Priya Shah", "status": "mitigated"})]},
        {"evidence": "R-030 | Credential rotation | High | Closed | Elena Ruiz | Rotation exercise completed and evidence archived.", "claims": [
            ("risk", "credential_rotation", {"severity": "high", "owner": "Elena Ruiz", "status": "closed"})]},
    ],
    "change_request_01.pdf": [
        {"evidence": "The change authority approved audit-log export as a launch requirement so enterprise administrators can retain event history.", "claims": [
            ("decision", "audit_log_export", {"decision": "add audit-log export as a launch requirement"})]},
        {"evidence": "Maya Chen owns delivery coordination for the audit-log export requirement, including acceptance evidence and release notes.", "claims": [
            ("owner", "audit_log_export", {"owner": "Maya Chen"})]},
    ],
    "change_request_02.docx": [
        {"evidence": "The change authority approved September 4, 2026 as the revised beta release date. The milestone remains planned while the packaging work closes.", "claims": [
            ("milestone", "beta_release", {"date": "2026-09-04", "status": "planned"})]},
        {"evidence": "Leah Morgan accepted ownership of the vendor-capacity risk. Severity remains High and the state is now Mitigating while the supplier adds a second delivery team.", "claims": [
            ("risk", "vendor_capacity", {"severity": "high", "owner": "Leah Morgan", "status": "mitigating"})]},
        {"evidence": "The authority also approved a customer-pilot checkpoint before broad production rollout.", "claims": [
            ("decision", "customer_pilot_checkpoint", {"decision": "customer pilot checkpoint before broad rollout"})]},
        {"evidence": "Deployment preparation remains delayed, and operations continues environment certification under the existing recovery plan.", "claims": [
            ("status", "deployment", {"status": "delayed"})]},
        {"evidence": "General-availability readiness remains planned for September 5, 2026, with Priya Shah retaining ownership of the readiness package.", "claims": [
            ("milestone", "general_availability", {"date": "2026-09-05", "status": "planned"}),
            ("owner", "general_availability", {"owner": "Priya Shah"})]},
    ],
}

INTENDED_NON_FACTS = {
    "project_charter.pdf": ["2025 discovery dates retained as history", "billing replacement explicitly out of scope", "budget and meeting cadence"],
    "project_plan.pdf": ["superseded June working dates", "tentative October contingency window", "team rituals and storage locations"],
    "release_plan.docx": ["zero-downtime aspiration", "unapproved single-wave alternative", "communications logistics"],
    "meeting_notes_01.docx": ["September 19 and October 3 proposals", "speaker opinions", "lunch and room logistics"],
    "meeting_notes_02.docx": ["hypothetical owner substitution", "parking-lot observability idea", "travel schedule"],
    "weekly_status_01.txt": ["ticket counts and support rota", "quoted prompt-injection chat message", "negative statement that it is not a project decision"],
    "risk_register.pdf": ["retired historical risk", "review instructions and scoring legend"],
    "change_request_01.pdf": ["rejected analytics-dashboard scope", "implementation alternatives"],
    "change_request_02.docx": ["rejected September 2 option", "unchanged contextual facts", "meeting and rollout logistics"],
}

EXPECTED_INITIAL = {
    "conflict": {"type": "deadline", "entity": "production_launch", "values": ["2026-09-12", "2026-09-26"]},
    "findings": [{"rule_id": "unowned_high_risk", "entity": "vendor_capacity"}],
    "non_findings": ["data_migration", "api_stability", "credential_rotation", "qa_automation", "completed_security_review", "completed_performance_qualification"],
    "status": {"project_atlas": "amber", "deployment": "delayed"},
    "brief_entities": {
        "milestones": ["beta_release", "general_availability", "security_review", "integration_rehearsal", "performance_qualification"],
        "owners": ["executive_sponsor", "beta_release", "general_availability", "integration_rehearsal", "audit_log_export"],
        "risks": ["vendor_capacity", "qa_automation", "data_migration", "api_stability", "credential_rotation"],
        "decisions": ["launch_rollout", "qa_exit", "audit_log_export"],
        "deadlines": [],  # launch remains unresolved until the human chooses a resolution
    },
}

EXPECTED_INCREMENTAL = {
    "changed": ["milestone:beta_release", "risk:vendor_capacity", "decision:customer_pilot_checkpoint"],
    "unchanged_context": ["status:deployment", "milestone:general_availability", "owner:general_availability"],
}
