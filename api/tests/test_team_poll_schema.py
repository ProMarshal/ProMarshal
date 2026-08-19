from app.team_poll.schema import (
    needs_question_rewrite,
    normalize_response_for_mode,
    parse_owner_question_and_mode,
)


def test_parse_owner_question_and_mode_defaults():
    question, mode = parse_owner_question_and_mode("ask team if everyone is ready")
    assert question.endswith("?")
    assert mode["response_format"] == "free_text"
    assert mode["deadline_mode"] == "default"


def test_parse_owner_question_and_mode_eod_and_format():
    question, mode = parse_owner_question_and_mode(
        'ask team cutover ready by eod format yes_no'
    )
    assert question.endswith("?")
    assert mode["response_format"] == "yes_no"
    assert mode["deadline_mode"] == "eod"


def test_normalize_response_yes_no():
    ok, payload = normalize_response_for_mode(response_format="yes_no", text="Yes")
    assert ok is True
    assert payload["value"] == "yes"


def test_normalize_response_single_choice_invalid():
    ok, payload = normalize_response_for_mode(
        response_format="single_choice",
        text="Unknown",
        options=["A", "B"],
    )
    assert ok is False
    assert payload["error"] == "invalid_choice"


def test_needs_question_rewrite_for_short_vague_question():
    assert needs_question_rewrite(
        question_text="Ready?",
        owner_text="ask team if everyone is ready for cutover tonight",
    ) is True


def test_needs_question_rewrite_false_for_clear_question():
    assert needs_question_rewrite(
        question_text="Is everyone ready for today's production cutover?",
        owner_text="ask team if everyone is ready for today's production cutover",
    ) is False
