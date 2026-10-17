import pytest

from inbox_sorter.engine import NoulAnswer
from inbox_sorter.engines import make_engine
from inbox_sorter.engines.fake import FakeEngine
from tests.engine_contract import CONTRACT_REQUEST, assert_conforms


def test_fake_engine_conforms():
    assert_conforms(FakeEngine())


def test_fake_engine_records_requests_and_uses_scripted_answers():
    scripted = FakeEngine(respond=lambda request: {"needs_reply": NoulAnswer(0.9)})

    response = scripted.decide(CONTRACT_REQUEST)

    assert scripted.requests == [CONTRACT_REQUEST]
    assert response.answers == {"needs_reply": NoulAnswer(0.9)}


def test_make_engine_builds_fake_without_loading_a_model():
    assert isinstance(make_engine("fake"), FakeEngine)


def test_make_engine_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown engine kind 'nope'"):
        make_engine("nope")


@pytest.mark.model
def test_clef_local_engine_conforms():
    engine = make_engine("clef-local")
    try:
        assert_conforms(engine)
    finally:
        engine.close()
