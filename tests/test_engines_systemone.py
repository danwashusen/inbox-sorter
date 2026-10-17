import pytest

from inbox_sorter.engine import ChoiceAnswer, NoulAnswer, ScoreAnswer
from inbox_sorter.engines.systemone import from_response_body, to_request_body
from tests.engine_contract import CONTRACT_REQUEST

# Shaped like a real clef systemone response from the smoke test.
RESPONSE = {
    "model": "clef",
    "answers": {
        "category": {
            "type": "choice",
            "choice": "personal",
            "confidence": 0.98,
            "probabilities": {"newsletter": 0.02, "personal": 0.98},
        },
        "needs_reply": {"type": "noul", "noul": 0.9817},
        "urgency": {
            "type": "score",
            "score": 0.985,
            "confidence": 0.9276,
            "legend": {"0": "Can wait", "1": "This week", "2": "Today"},
            "probabilities": {"0": 0.0437, "1": 0.9276, "2": 0.0287},
        },
    },
    "usage": {"input_tokens": 392, "output_tokens": 0},
}


def test_request_body_uses_system_one_question_shapes():
    body = to_request_body(CONTRACT_REQUEST, model="clef")

    assert body["model"] == "clef"
    assert body["state"] == CONTRACT_REQUEST.state
    assert body["questions"]["category"] == {
        "type": "choice",
        "criteria": {"personal": "Written by a person", "newsletter": "Bulk mailing"},
    }
    assert body["questions"]["needs_reply"] == {
        "type": "noul",
        "instructions": "Does the sender expect a reply?",
    }
    assert body["questions"]["urgency"] == {
        "type": "score",
        "criteria": ["Can wait", "This week", "Today"],
    }


def test_response_body_becomes_neutral_answers():
    response = from_response_body(CONTRACT_REQUEST, RESPONSE, engine="clef-local:test")

    assert response.engine == "clef-local:test"
    assert response.answers["category"] == ChoiceAnswer(
        "personal", 0.98, {"newsletter": 0.02, "personal": 0.98}
    )
    assert response.answers["needs_reply"] == NoulAnswer(0.9817)
    assert response.answers["urgency"] == ScoreAnswer(0.985, (0.0437, 0.9276, 0.0287))


def test_missing_answer_is_an_error():
    body = {"answers": {k: v for k, v in RESPONSE["answers"].items() if k != "urgency"}}

    with pytest.raises(ValueError, match="urgency: missing"):
        from_response_body(CONTRACT_REQUEST, body, engine="x")


def test_wrong_answer_type_is_an_error():
    answers = dict(RESPONSE["answers"], needs_reply={"type": "choice", "choice": "yes"})

    with pytest.raises(ValueError, match="needs_reply: expected a noul answer"):
        from_response_body(CONTRACT_REQUEST, {"answers": answers}, engine="x")
