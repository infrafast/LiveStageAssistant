from voice_assistant.realtime.corpus import CorpusCase, CorpusExpect, evaluate_case, parse_case


def test_parse_case_defaults():
    case = parse_case({"id": "read", "text": "state?"})
    assert case.case_id == "read"
    assert case.text == "state?"
    assert case.expect.min_tool_calls == 1
    assert case.expect.max_tool_calls is None


def test_evaluate_case_accepts_generic_tool_contract():
    case = CorpusCase(
        case_id="read",
        text="state?",
        expect=CorpusExpect(
            min_tool_calls=1,
            max_tool_calls=2,
            required_tools=("resolve_target", "read_state"),
            forbidden_tools=("broad_status",),
            max_latency_ms=2000.0,
            max_cost_usd=0.05,
        ),
    )
    result = {
        "answer": "ok",
        "latency_ms": {"turn_to_final_response": 1200.0},
        "usage": {
            "tool_calls": 2,
            "tools": [{"name": "resolve_target"}, {"name": "read_state"}],
        },
        "cost_usd": {"measured_provider_usage": 0.01},
    }
    assertion = evaluate_case(case, result)
    assert assertion.ok
    assert assertion.failures == ()


def test_evaluate_case_reports_all_failures():
    case = CorpusCase(
        case_id="bad",
        text="state?",
        expect=CorpusExpect(
            min_tool_calls=2,
            max_tool_calls=2,
            required_tools=("read_state",),
            forbidden_tools=("broad_status",),
            require_answer=True,
            max_latency_ms=1000.0,
            max_cost_usd=0.01,
        ),
    )
    result = {
        "answer": "",
        "latency_ms": {"turn_to_final_response": 1500.0},
        "usage": {
            "tool_calls": 1,
            "tools": [{"name": "broad_status"}],
        },
        "cost_usd": {"measured_provider_usage": 0.02},
    }
    assertion = evaluate_case(case, result)
    assert not assertion.ok
    joined = "\n".join(assertion.failures)
    assert "tool_calls 1 < minimum 2" in joined
    assert "required tool not called: read_state" in joined
    assert "forbidden tool called: broad_status" in joined
    assert "missing final answer" in joined
    assert "latency" in joined
    assert "cost" in joined
