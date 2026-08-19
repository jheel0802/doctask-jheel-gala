from project_evidence.workflow import failure_guidance


def test_http_400_is_not_reported_as_a_budget_failure():
    reason, action = failure_guidance("RuntimeError: OpenAI request failed with HTTP 400; code=invalid_json_schema")
    assert "not a spending-budget failure" in reason
    assert "schema" in action
    assert "raise" not in action.lower()


def test_application_budget_has_distinct_guidance():
    reason, action = failure_guidance("Run cost limit ($0.25) reached")
    assert "per-run guard" in reason
    assert "Raise" in action
