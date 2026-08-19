from __future__ import annotations

import re
from typing import Any


MISSING_OWNER_VALUES = {"", "none", "null", "nil", "n/a", "na", "unassigned", "not assigned", "unknown"}


def normalize_claim_value(value: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize narrow cross-provider representation differences before comparison/storage."""
    normalized = dict(value)
    owner = normalized.get("owner")
    if owner is None or (isinstance(owner, str) and owner.strip().lower() in MISSING_OWNER_VALUES):
        if "owner" in normalized:
            normalized["owner"] = None
    for key, item in list(normalized.items()):
        if isinstance(item, str) and key not in {"date", "owner"}:
            # Comparison values are canonical; source evidence and display text
            # remain untouched on the claim/provenance record.
            normalized[key] = re.sub(r"[\s.]+$", "", re.sub(r"\s+", " ", item.strip().lower()))
    return normalized


def merge_compatible_values(values: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        for key, item in normalize_claim_value(value).items():
            if key not in merged or merged[key] is None:
                merged[key] = item
    return merged


def is_rejected_or_non_project_fact(fact_type: str, claim: str, value: dict[str, Any]) -> bool:
    """Reject meta/negative labels that explicitly deny being a supported project fact."""
    if fact_type != "decision":
        return False
    decision = str(value.get("decision", "")).strip().lower()
    text = claim.strip().lower()
    negative_phrases = ("not a project decision", "not a decision", "no decision was made", "was not decided")
    return any(phrase in decision or phrase in text for phrase in negative_phrases)
