from __future__ import annotations

from datetime import date
from typing import Any


def default_ruleset() -> dict[str, Any]:
    return {
        "name": "Default project rules",
        "rules": [
            {
                "id": "unowned_high_risk",
                "title": "High-severity risk has no owner.",
                "enabled": True,
                "fact_type": "risk",
                "conditions": [
                    {"field": "severity", "op": "eq", "value": "high"},
                    {"field": "owner", "op": "missing"},
                ],
            },
            {
                "id": "overdue_milestone",
                "title": "Milestone is overdue and incomplete.",
                "enabled": True,
                "fact_type": "milestone",
                "conditions": [
                    {"field": "date", "op": "before_today"},
                    {"field": "status", "op": "not_in", "value": ["complete", "completed", "done"]},
                ],
            },
            {
                "id": "red_project_status",
                "title": "Project status is red.",
                "enabled": True,
                "fact_type": "status",
                "conditions": [{"field": "status", "op": "eq", "value": "red"}],
            },
        ],
    }


def validate_ruleset(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("rules"), list):
        raise ValueError("ruleset must contain a rules list")
    allowed_ops = {"eq", "missing", "not_in", "before_today"}
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in value["rules"]:
        if not isinstance(rule, dict) or not rule.get("id") or not rule.get("fact_type"):
            raise ValueError("each rule needs id and fact_type")
        rule_id = str(rule["id"])
        if rule_id in seen:
            raise ValueError(f"duplicate rule id: {rule_id}")
        seen.add(rule_id)
        conditions = rule.get("conditions", [])
        if not conditions or any(c.get("op") not in allowed_ops or not c.get("field") for c in conditions):
            raise ValueError(f"rule {rule_id} has unsupported conditions")
        clean.append({
            "id": rule_id,
            "title": str(rule.get("title") or rule_id.replace("_", " ").title()),
            "enabled": bool(rule.get("enabled", True)),
            "fact_type": str(rule["fact_type"]),
            "conditions": conditions,
        })
    return {"name": str(value.get("name") or "Corpus rules"), "rules": clean}


def rule_matches(rule: dict[str, Any], fact_type: str, value: dict[str, Any]) -> bool:
    if not rule.get("enabled", True) or rule.get("fact_type") != fact_type:
        return False
    for condition in rule.get("conditions", []):
        actual = value.get(condition["field"])
        op = condition["op"]
        expected = condition.get("value")
        if op == "eq" and str(actual).lower() != str(expected).lower():
            return False
        if op == "missing" and actual not in (None, "", "unassigned", "unknown"):
            return False
        if op == "not_in" and str(actual).lower() in {str(v).lower() for v in expected or []}:
            return False
        if op == "before_today":
            try:
                if date.fromisoformat(str(actual)) >= date.today():
                    return False
            except (TypeError, ValueError):
                return False
    return True
