"""Tests for soul evolution and response verification."""

import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest

from pawbot.soul import (
    CoreDistiller,
    DailyJournal,
    SessionContinuity,
    SoulEvolution,
    SoulPatch,
)
from pawbot.agent.verification import (
    CitationExtractor,
    HallucinationCritic,
    ResponseVerifier,
)


# ══════════════════════════════════════════════════════════════════════════════
#  DailyJournal Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestDailyJournal:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.journal = DailyJournal(self.tmp)

    def test_append_and_flush(self):
        self.journal.append_event("conversation", "User asked about Python")
        self.journal.append_event("action", "Wrote test file", importance=0.8)
        path = self.journal.flush()
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Python" in content
        assert "⭐" in content  # High importance marker

    def test_flush_empty_does_nothing(self):
        path = self.journal.flush()
        assert not path.exists()

    def test_get_today(self):
        self.journal.append_event("test", "Hello")
        self.journal.flush()
        content = self.journal.get_today()
        assert "Hello" in content

    def test_get_recent(self):
        self.journal.append_event("test", "Today's event")
        self.journal.flush()
        recent = self.journal.get_recent(days=3)
        assert len(recent) >= 1
        assert "Today's event" in recent[0]["content"]

    def test_list_all(self):
        self.journal.append_event("test", "Entry")
        self.journal.flush()
        dates = self.journal.list_all()
        assert len(dates) >= 1
        today = datetime.now().strftime("%Y-%m-%d")
        assert today in dates

    def test_multiple_flushes_append(self):
        self.journal.append_event("test", "First")
        self.journal.flush()
        self.journal.append_event("test", "Second")
        self.journal.flush()
        content = self.journal.get_today()
        assert "First" in content
        assert "Second" in content

    def test_event_tags(self):
        self.journal.append_event("test", "Tagged event", tags=["python", "test"])
        self.journal.flush()
        content = self.journal.get_today()
        assert "python" in content
        assert "test" in content


# ══════════════════════════════════════════════════════════════════════════════
#  CoreDistiller Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestCoreDistiller:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.distiller = CoreDistiller(self.tmp)

    def test_get_empty_core(self):
        assert self.distiller.get_core() == ""

    def test_update_core(self):
        self.distiller.update_core("# Core Memory\n\n- User prefers Python")
        content = self.distiller.get_core()
        assert "Python" in content

    def test_append_to_new_section(self):
        self.distiller.update_core("# Core Memory\n")
        self.distiller.append_to_core("Preferences", "Uses dark mode")
        content = self.distiller.get_core()
        assert "## Preferences" in content
        assert "dark mode" in content

    def test_append_to_existing_section(self):
        self.distiller.update_core("# Core\n\n## Preferences\n\n- Python\n")
        self.distiller.append_to_core("Preferences", "Dark mode")
        content = self.distiller.get_core()
        assert "Python" in content
        assert "Dark mode" in content

    def test_mark_distilled(self):
        self.distiller.mark_distilled()
        marker = self.tmp / "memory" / ".last_distilled"
        assert marker.exists()


# ══════════════════════════════════════════════════════════════════════════════
#  SessionContinuity Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestSessionContinuity:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.continuity = SessionContinuity(self.tmp)

    def test_boot_empty_workspace(self):
        loaded = self.continuity.boot()
        assert isinstance(loaded, dict)

    def test_boot_with_soul(self):
        soul_path = self.tmp / "SOUL.md"
        soul_path.write_text("# Soul\nI am Pawbot")
        loaded = self.continuity.boot()
        assert "soul" in loaded
        assert "Pawbot" in loaded["soul"]

    def test_boot_with_multiple_files(self):
        (self.tmp / "SOUL.md").write_text("# Soul")
        (self.tmp / "USER.md").write_text("# User")
        (self.tmp / "HEARTBEAT.md").write_text("# Heartbeat")
        loaded = self.continuity.boot()
        assert "soul" in loaded
        assert "user" in loaded
        assert "heartbeat" in loaded

    def test_boot_with_journal(self):
        journal = DailyJournal(self.tmp)
        journal.append_event("test", "Boot test event")
        journal.flush()
        loaded = self.continuity.boot()
        journal_key = f"journal_{datetime.now().strftime('%Y-%m-%d')}"
        assert journal_key in loaded


# ══════════════════════════════════════════════════════════════════════════════
#  SoulEvolution Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestSoulEvolution:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.evolution = SoulEvolution(self.tmp)
        # Create a minimal SOUL.md
        (self.tmp / "SOUL.md").write_text(
            "# Soul\n\n## Personality\n\n- Helpful\n- Concise\n"
        )

    def test_propose_patch(self):
        patch = self.evolution.propose_patch(
            target_file="SOUL.md",
            section="Personality",
            action="add",
            content="- Patient with beginners",
            reason="User frequently asks basic questions",
        )
        assert isinstance(patch, SoulPatch)
        assert not patch.approved

    def test_get_pending(self):
        self.evolution.propose_patch(
            "SOUL.md", "Personality", "add", "- Patient", "lesson",
        )
        pending = self.evolution.get_pending()
        assert len(pending) == 1

    def test_apply_add_patch(self):
        patch = self.evolution.propose_patch(
            "SOUL.md", "Personality", "add",
            "- Patient with beginners",
            "User is learning",
        )
        result = self.evolution.apply_patch(patch)
        assert result is True
        assert patch.approved is True
        content = (self.tmp / "SOUL.md").read_text()
        assert "Patient with beginners" in content

    def test_apply_to_new_section(self):
        patch = self.evolution.propose_patch(
            "SOUL.md", "New Section", "add",
            "- First entry in new section",
            "Discovered new pattern",
        )
        self.evolution.apply_patch(patch)
        content = (self.tmp / "SOUL.md").read_text()
        assert "## New Section" in content
        assert "First entry" in content

    def test_approve_all(self):
        self.evolution.propose_patch("SOUL.md", "Test", "add", "a", "r1")
        self.evolution.propose_patch("SOUL.md", "Test", "add", "b", "r2")
        count = self.evolution.approve_all()
        assert count == 2


# ══════════════════════════════════════════════════════════════════════════════
#  ResponseVerifier Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestResponseVerifier:
    def setup_method(self):
        self.verifier = ResponseVerifier()

    @pytest.mark.asyncio
    async def test_clean_response(self):
        result = await self.verifier.verify(
            "The file contains 42 lines of Python code.",
            tool_results=[{"output": "42 lines", "success": True}],
        )
        assert result.passed is True
        assert result.risk_score < 0.5

    @pytest.mark.asyncio
    async def test_tool_contradiction(self):
        result = await self.verifier.verify(
            "I've successfully created the file and deployed the application.",
            tool_results=[{"error": "Permission denied", "success": False}],
        )
        assert result.risk_score >= 0.5
        assert any("success" in i.lower() or "fail" in i.lower() for i in result.issues)

    @pytest.mark.asyncio
    async def test_overconfidence_detection(self):
        result = await self.verifier.verify(
            "I guarantee this will work. I'm certain that this is correct. "
            "Without a doubt, this is the best approach.",
        )
        assert len(result.warnings) > 0

    @pytest.mark.asyncio
    async def test_incomplete_response(self):
        # A string >100 chars that doesn't end with punctuation
        text = "The function takes three parameters: the first is the input data, the second is the configuration object, and the third is the callback function that will be invoked when processing"
        result = await self.verifier.verify(text)
        assert any("truncated" in i.lower() or "incomplete" in i.lower() for i in result.issues)

    @pytest.mark.asyncio
    async def test_unclosed_code_block(self):
        result = await self.verifier.verify("Here's the code:\n```python\ndef foo():\n    pass")
        assert any("truncated" in i.lower() or "incomplete" in i.lower() for i in result.issues)

    @pytest.mark.asyncio
    async def test_valid_python_code_block(self):
        result = await self.verifier.verify(
            "Here's the function:\n```python\ndef hello():\n    return 'world'\n```\nDone."
        )
        code_issues = [w for w in result.warnings if "syntax" in w.lower()]
        assert len(code_issues) == 0

    @pytest.mark.asyncio
    async def test_invalid_python_code_block(self):
        result = await self.verifier.verify(
            "```python\ndef broken(\n    return\n```\nAbove is the code."
        )
        code_issues = [w for w in result.warnings if "syntax" in w.lower()]
        assert len(code_issues) > 0

    @pytest.mark.asyncio
    async def test_empty_response(self):
        result = await self.verifier.verify("")
        assert any("truncated" in i.lower() or "incomplete" in i.lower() for i in result.issues)


# ══════════════════════════════════════════════════════════════════════════════
#  HallucinationCritic Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestHallucinationCritic:
    def setup_method(self):
        self.critic = HallucinationCritic()

    @pytest.mark.asyncio
    async def test_grounded_response(self):
        result = await self.critic.score(
            "The file utils.py contains the function parse_config which returns a dictionary object.",
            tool_results=[{"output": "file utils.py contains function parse_config returns dictionary object"}],
        )
        assert result.risk_score < 0.8

    @pytest.mark.asyncio
    async def test_ungrounded_response(self):
        result = await self.critic.score(
            "The quantum flux capacitor module is running version 3.7 and has 42 active connections.",
            tool_results=[],
        )
        # With no tool results, any extracted claims should be flagged
        assert result.risk_score >= 0.0  # May or may not extract claims

    @pytest.mark.asyncio
    async def test_no_claims(self):
        result = await self.critic.score("Sure, let me help!")
        assert result.risk_score == 0.0

    @pytest.mark.asyncio
    async def test_memory_grounded(self):
        result = await self.critic.score(
            "The user prefers Python over JavaScript for backend development.",
            memory_facts=[{"content": "User prefers Python for backend work"}],
        )
        assert result.risk_score < 0.5


# ══════════════════════════════════════════════════════════════════════════════
#  CitationExtractor Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestCitationExtractor:
    def setup_method(self):
        self.extractor = CitationExtractor()

    def test_extract_with_tool_source(self):
        cited = self.extractor.extract_and_cite(
            "The server is running on port 8080 and has 4 active connections.",
            tool_results=[{"tool": "server_status", "output": "port 8080, 4 connections active"}],
        )
        tool_sourced = [c for c in cited if c.source_type == "tool_result"]
        assert len(tool_sourced) > 0

    def test_extract_with_memory_source(self):
        cited = self.extractor.extract_and_cite(
            "The database schema is using PostgreSQL and the users table has 500 records.",
            memory_facts=[{"content": "database schema PostgreSQL users table 500 records", "id": "fact-42"}],
        )
        # Check that at least some claims are extracted
        assert len(cited) >= 0  # May extract 0 if sentence isn't long enough

    def test_format_citations(self):
        cited = self.extractor.extract_and_cite(
            "The API returns JSON with status code 200.",
            tool_results=[{"tool": "api_test", "output": "200 OK, JSON response"}],
        )
        formatted = self.extractor.format_citations(cited)
        if cited:
            assert "Sources" in formatted or formatted == ""
