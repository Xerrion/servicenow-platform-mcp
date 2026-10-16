"""Published argument examples match the current native tool schemas."""

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[1]


def test_current_argument_examples_match_native_schemas() -> None:
    contracts = json.loads(
        (ROOT / "tests/fixtures/native_tool_schemas.json").read_text()
    )
    checked = 0
    for relative in ("docs/wiki/Tool-Reference.md", "docs/agent-recipes.md"):
        text = (ROOT / relative).read_text()
        for block in re.finditer(r"```json\n(.*?)\n```", text, re.DOTALL):
            prior = text[: block.start()]
            names = list(re.finditer(r"(?:Arguments for|###)\s+`([^`]+)`", prior))
            if not names:
                continue
            tool = names[-1].group(1)
            assert tool in contracts, (relative, tool)
            Draft202012Validator(contracts[tool]["input"]).validate(
                json.loads(block.group(1))
            )
            checked += 1
    assert checked >= 15
