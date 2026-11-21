"""The read-only gate: writes are off by default and need a dry run before they're sent."""

from __future__ import annotations

import pytest
from mcp import Client

from ngpvan_mcp import tools as t
from ngpvan_mcp.config import Config


def writes_on() -> Config:
    return Config(audit_log=None, writes={"apply_canvass_response": True})


async def test_write_tool_is_not_even_listed_by_default(server_factory):
    async with Client(server_factory()) as c:
        names = {tool.name for tool in (await c.list_tools()).tools}
    assert "apply_canvass_response" not in names
    assert len(names) == 13


async def test_every_listed_tool_is_marked_read_only_by_default(server_factory):
    async with Client(server_factory()) as c:
        for tool in (await c.list_tools()).tools:
            assert tool.annotations.read_only_hint is True, tool.name


async def test_calling_the_hidden_write_tool_fails(server_factory, fake):
    async with Client(server_factory()) as c:
        result = await c.call_tool("apply_canvass_response", {"van_id": 100200101, "contact_type_id": 2,
                                                              "result_code_id": 1, "dry_run": False})
    assert result.is_error
    assert fake.written_canvass_responses == []
    assert not [r for r in fake.requests if r.method == "POST"]


async def test_toolbox_refuses_a_write_when_config_is_off(tb, fake):
    """Second lock: even called directly, the function checks the config."""
    with pytest.raises(t.WriteDisabledError):
        await t.apply_canvass_response(tb, 100200101, contact_type_id=2, result_code_id=1, dry_run=False)
    assert fake.written_canvass_responses == []


async def test_enabled_write_dry_run_sends_nothing(server_factory, fake):
    async with Client(server_factory(writes_on())) as c:
        names = {tool.name for tool in (await c.list_tools()).tools}
        assert "apply_canvass_response" in names
        result = await c.call_tool("apply_canvass_response", {
            "van_id": 100200101, "contact_type_id": 2, "survey_question_id": 5201, "survey_response_id": 52011})
    assert not result.is_error
    body = result.structured_content
    assert body["dry_run"] is True
    assert body["would_send"]["path"] == "/people/100200101/canvassResponses"
    assert body["would_send"]["body"]["responses"] == [
        {"type": "SurveyResponse", "surveyQuestionId": 5201, "surveyResponseId": 52011}]
    assert fake.written_canvass_responses == []


async def test_enabled_write_needs_the_dry_run_token(server_factory, fake):
    args = {"van_id": 100200101, "contact_type_id": 2, "result_code_id": 1}
    async with Client(server_factory(writes_on())) as c:
        no_token = await c.call_tool("apply_canvass_response", {**args, "dry_run": False})
        assert no_token.is_error and "dry_run=true first" in no_token.content[0].text
        preview = (await c.call_tool("apply_canvass_response", args)).structured_content
        wrong = await c.call_tool("apply_canvass_response", {**args, "dry_run": False, "confirm_token": "0" * 16})
        assert wrong.is_error
        # A token is tied to the exact change: it can't be reused for a different result code.
        other = await c.call_tool("apply_canvass_response", {**args, "result_code_id": 2, "dry_run": False,
                                                             "confirm_token": preview["confirm_token"]})
        assert other.is_error
        assert fake.written_canvass_responses == []
        sent = await c.call_tool("apply_canvass_response", {**args, "dry_run": False,
                                                            "confirm_token": preview["confirm_token"]})
    assert not sent.is_error and sent.structured_content["applied"] is True
    assert fake.written_canvass_responses == [
        {"vanId": 100200101, "canvassContext": {"contactTypeId": 2, "inputTypeId": 11},
         "resultCodeId": 1, "responses": None}]


async def test_dry_run_names_the_change(van):
    tb = t.Toolbox(van=van, config=writes_on())
    out = await t.apply_canvass_response(tb, 100200149, contact_type_id=2, survey_question_id=5201,
                                         survey_response_id=52011)
    assert out["summary"] == {"van_id": 100200149, "person": "Maria L.", "contact_type": "Walk",
                              "survey_question": "Measure 3 Support", "survey_response": "1 Strong Yes"}


@pytest.mark.parametrize("args,message", [
    ({"van_id": 12345, "contact_type_id": 2, "result_code_id": 1}, "no person with VAN ID 12345"),
    ({"contact_type_id": 99, "result_code_id": 1}, "no contact type 99"),
    ({"contact_type_id": 2, "result_code_id": 777}, "no result code 777"),
    ({"contact_type_id": 2, "survey_question_id": 67890, "survey_response_id": 1}, "no survey question 67890"),
    ({"contact_type_id": 2, "survey_question_id": 5201, "survey_response_id": 52021}, "isn't a response to"),
    ({"contact_type_id": 2, "activist_code_id": 4099}, "no active activist code 4099"),
])
async def test_dry_run_refuses_ids_that_dont_exist(van, fake, args, message):
    """A model can invent plausible IDs; the dry run must not hand out a token for them."""
    tb = t.Toolbox(van=van, config=writes_on())
    args = {"van_id": 100200101, **args}
    with pytest.raises(t.ToolInputError, match=message):
        await t.apply_canvass_response(tb, **args)
    assert not [r for r in fake.requests if r.method == "POST"]


@pytest.mark.parametrize("args,message", [
    ({"contact_type_id": 2}, "give a result_code_id"),
    ({"contact_type_id": 2, "result_code_id": 1, "activist_code_id": 4101}, "not both"),
    ({"contact_type_id": 2, "survey_question_id": 5201}, "needs both"),
    ({"contact_type_id": 2, "result_code_id": 1, "date_canvassed": "Saturday"}, "must be a date"),
])
async def test_write_input_is_validated_before_anything_is_sent(van, args, message, fake):
    tb = t.Toolbox(van=van, config=writes_on())
    with pytest.raises(t.ToolInputError, match=message):
        await t.apply_canvass_response(tb, 100200101, **args)
    assert not [r for r in fake.requests if r.method == "POST"]
