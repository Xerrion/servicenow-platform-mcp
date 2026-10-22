"""Capability and policy imports cannot depend on the MCP transport."""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "src/servicenow_mcp"


@pytest.mark.parametrize("directory", ["capabilities", "policy", "platform/metadata"])
def test_lower_layers_have_no_transport_imports(directory: str) -> None:
    forbidden = (
        "mcp",
        "servicenow_mcp.mcp",
        "servicenow_mcp.bootstrap",
        "servicenow_mcp.tools",
        "servicenow_mcp.response",
    )
    violations = []
    for path in (SOURCE / directory).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                if any(
                    name == prefix or name.startswith(prefix + ".")
                    for prefix in forbidden
                ):
                    violations.append(
                        f"{path.relative_to(SOURCE)}:{node.lineno}: {name}"
                    )
    assert not violations, "\n".join(violations)
