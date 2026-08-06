"""ToolCallParser unit tests — only unique tests not covered by test_tool_parser_extensive."""

import pytest
from api_service.agent.tool_parser import ToolCallParser


@pytest.fixture
def parser():
    return ToolCallParser()


def test_parse_tool_arguments_invalid(parser):
    assert parser.parse_tool_arguments("invalid-json") == {}
    assert parser.parse_tool_arguments(123) == {}
