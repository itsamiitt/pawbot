from pawbot.agent.tools.shell import ExecTool


def test_exec_tool_blocks_prod_restricted_command(monkeypatch):
    monkeypatch.setenv("PAWBOT_ENV", "production")
    tool = ExecTool(timeout=2)
    result = tool._guard_command("git push --force origin main", ".")
    assert "production safety policy" in (result or "")


def test_exec_tool_allows_non_restricted_in_prod(monkeypatch):
    monkeypatch.setenv("PAWBOT_ENV", "production")
    tool = ExecTool(timeout=2)
    result = tool._guard_command("echo hello", ".")
    assert result is None
