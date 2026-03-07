"""
tests/test_dead_code_removal.py

Verifies dead-code fixes from Section 3.
Run: pytest tests/test_dead_code_removal.py -v
"""

from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_app_control_no_pil_image_import():
    """Unused PIL.Image import should be removed from app_control server."""
    source = _read("mcp-servers/app_control/server.py")
    assert "from PIL import Image" not in source


def test_matrix_no_content_repository_config_error_import():
    """Unused ContentRepositoryConfigError import should be removed from matrix channel."""
    source = _read("pawbot/channels/matrix.py")
    assert "ContentRepositoryConfigError" not in source


def test_qq_no_duplicate_c2c_message_import():
    """C2CMessage should appear in at most one import line."""
    source = _read("pawbot/channels/qq.py")
    import_lines = [
        line for line in source.splitlines() if "import" in line and "C2CMessage" in line
    ]
    assert len(import_lines) <= 1, f"Duplicate C2CMessage import lines: {import_lines}"


def test_browser_no_dead_variable_assignments():
    """Dead variable assignments should not exist in browser server."""
    source = _read("mcp-servers/browser/server.py")
    assert re.search(r"^\s*restore_session\s*=", source, re.MULTILINE) is None
    assert re.search(r"^\s*full_page\s*=", source, re.MULTILINE) is None

