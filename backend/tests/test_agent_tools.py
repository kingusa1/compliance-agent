from app.agent.tool_handlers import (
    find_evidence,
    verify_quote,
    check_speaker,
    get_word_context,
    get_similar_learnings,
    flag_low_confidence,
    ToolContext,
)
from app.models import AgentLearning


TRANSCRIPT = (
    "[00:05] Agent: Hi, is it Adam speaking? "
    "[00:07] Customer: Yeah, speaking. "
    "[00:09] Agent: Adam, it's Alex calling back. I said I'd look into the E.ON rates for you. "
    "[00:20] Agent: Good news is I can keep your standing charge at 30 pence per day. "
    "[00:28] Customer: Okay, that's fine. "
    "[00:32] Agent: You have one day to cancel this contract under the cooling off period."
)

WORD_DATA = [
    {"word": "Hi", "speaker": "A", "start": 5.0, "end": 5.2, "confidence": 0.95},
    {"word": "is", "speaker": "A", "start": 5.2, "end": 5.3, "confidence": 0.95},
    {"word": "it", "speaker": "A", "start": 5.3, "end": 5.4, "confidence": 0.94},
    {"word": "Adam", "speaker": "A", "start": 5.4, "end": 5.7, "confidence": 0.91},
    {"word": "speaking", "speaker": "A", "start": 5.7, "end": 6.0, "confidence": 0.96},
    {"word": "Yeah", "speaker": "B", "start": 7.1, "end": 7.4, "confidence": 0.97},
    {"word": "speaking", "speaker": "B", "start": 7.4, "end": 7.8, "confidence": 0.95},
    {"word": "Okay", "speaker": "B", "start": 28.0, "end": 28.3, "confidence": 0.99},
    {"word": "thats", "speaker": "B", "start": 28.3, "end": 28.5, "confidence": 0.93},
    {"word": "fine", "speaker": "B", "start": 28.5, "end": 28.7, "confidence": 0.97},
]


def _ctx(db=None):
    return ToolContext(
        transcript=TRANSCRIPT,
        word_data=WORD_DATA,
        supplier="E.ON Next",
        agent_speaker_label="A",
        customer_speaker_label="B",
        db=db,
    )


def test_find_evidence_high_similarity():
    result = find_evidence(_ctx(), query="standing charge 30 pence per day")
    assert result["verified"] is True
    assert result["similarity"] >= 0.75
    assert "30 pence per day" in result["best_match"]


def test_find_evidence_not_found():
    result = find_evidence(_ctx(), query="apocalyptic volcanic winter discount")
    assert result["verified"] is False
    assert result["similarity"] < 0.75


def test_verify_quote_exact_substring():
    r = verify_quote(_ctx(), quote="standing charge at 30 pence per day")
    assert r["exact_match"] is True


def test_verify_quote_not_found():
    r = verify_quote(_ctx(), quote="this exact string is not in the transcript")
    assert r["exact_match"] is False


def test_check_speaker_customer_confirmation():
    r = check_speaker(_ctx(), quote="Okay that's fine", expected="Customer")
    assert r["verified"] is True
    assert r["speaker"] == "Customer"


def test_check_speaker_mismatch():
    r = check_speaker(_ctx(), quote="Hi is it Adam speaking", expected="Customer")
    assert r["verified"] is False
    assert r["speaker"] == "Agent"


def test_get_word_context_window():
    r = get_word_context(_ctx(), position=28.0, window_seconds=2.0)
    text = " ".join(w["word"] for w in r["words"])
    assert "Okay" in text
    assert "fine" in text


def test_flag_low_confidence_returns_ack():
    r = flag_low_confidence(_ctx(), checkpoint="CP-test", reason="insufficient evidence")
    assert r["flagged"] is True
    assert r["verified"] is True
    assert r["checkpoint"] == "CP-test"


def test_get_similar_learnings_empty_db(test_db):
    r = get_similar_learnings(
        _ctx(db=test_db),
        supplier="E.ON Next",
        checkpoint_name="Agent confirms DOB",
        limit=3,
    )
    assert r["count"] == 0
    assert r["learnings"] == []


def test_get_similar_learnings_returns_matches(test_db):
    test_db.add(AgentLearning(
        supplier="E.ON Next",
        checkpoint_name="Agent confirms DOB",
        pattern="yes/no DOB without confirmation",
        agent_verdict="pass",
        human_verdict="fail",
        lesson="need explicit customer yes",
    ))
    test_db.add(AgentLearning(
        supplier="British Gas",
        checkpoint_name="Agent confirms DOB",
        pattern="different supplier",
        agent_verdict="pass",
        human_verdict="pass",
        lesson="",
    ))
    test_db.commit()

    r = get_similar_learnings(
        _ctx(db=test_db),
        supplier="E.ON Next",
        checkpoint_name="Agent confirms DOB",
        limit=3,
    )
    assert r["count"] == 1
    assert r["learnings"][0]["lesson"] == "need explicit customer yes"


def test_find_evidence_rejects_non_string_query():
    r = find_evidence(_ctx(), query=None)
    assert "error" in r


def test_verify_quote_rejects_empty_string():
    r = verify_quote(_ctx(), quote="   ")
    assert "error" in r


def test_check_speaker_rejects_invalid_expected():
    r = check_speaker(_ctx(), quote="yeah", expected="Martian")
    assert "error" in r


def test_check_speaker_handles_apostrophe_normalization():
    """'That's fine' (with apostrophe) should match word_data 'thats' (without)."""
    ctx = ToolContext(
        transcript="dummy",
        word_data=[
            {"word": "Thats", "speaker": "B", "start": 0.0, "end": 0.2},
            {"word": "fine", "speaker": "B", "start": 0.2, "end": 0.4},
        ],
        supplier="Test",
        agent_speaker_label="A",
        customer_speaker_label="B",
    )
    r = check_speaker(ctx, quote="that's fine", expected="Customer")
    assert r["verified"] is True


from app.agent.tools import TOOL_SCHEMAS, dispatch_tool


def test_tool_schemas_have_required_fields():
    required_tool_names = {
        "find_evidence", "verify_quote", "check_speaker",
        "get_word_context", "flag_low_confidence", "get_similar_learnings",
    }
    schema_names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert schema_names == required_tool_names

    for tool in TOOL_SCHEMAS:
        assert tool["type"] == "function"
        fn = tool["function"]
        assert "name" in fn and "description" in fn and "parameters" in fn
        assert fn["parameters"]["type"] == "object"
        assert "properties" in fn["parameters"]


def test_dispatch_tool_routes_find_evidence():
    result = dispatch_tool(
        _ctx(),
        name="find_evidence",
        arguments={"query": "standing charge 30 pence"},
    )
    assert result["verified"] is True


def test_dispatch_tool_unknown_name_returns_error():
    result = dispatch_tool(
        _ctx(),
        name="nonexistent_tool",
        arguments={"x": 1},
    )
    assert "error" in result
