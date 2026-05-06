"""Unit tests for the deterministic coref gate."""

import pytest

from preprocessing.coref_gate import needs_coref


_PRIOR_TURN = [{"role": "user", "content": "Tell me about COMS4705"}]
_TWO_PRIOR_TURNS = [
    {"role": "user", "content": "Show me Spring 2026 AI electives"},
    {"role": "assistant", "content": "Found 5 courses"},
]


class TestNeedsCoref:
    def test_first_turn_skips(self):
        """Empty history → never run coref."""
        assert needs_coref("What is the prereq for COMS3134?", []) is False

    def test_self_contained_skips(self):
        assert needs_coref(
            "What time does CS101 meet on Mondays",
            _PRIOR_TURN,
        ) is False

    def test_english_pronoun_fires(self):
        assert needs_coref("What is its prereq?", _PRIOR_TURN) is True

    def test_english_definite_reference_fires(self):
        assert needs_coref(
            "Tell me more about the course",
            _PRIOR_TURN,
        ) is True

    def test_chinese_pronoun_fires(self):
        assert needs_coref("它的 prereq 是什么", _PRIOR_TURN) is True

    def test_chinese_ellipsis_fires(self):
        assert needs_coref("再查一下避开周五的", _TWO_PRIOR_TURNS) is True

    def test_chinese_definite_reference_fires(self):
        assert needs_coref("那门课的老师是谁", _PRIOR_TURN) is True

    def test_short_mid_conversation_fires(self):
        """≤3 words after at least 2 prior messages → likely elliptical."""
        assert needs_coref("CS101 fall", _TWO_PRIOR_TURNS) is True

    def test_short_first_turn_does_not_fire(self):
        """Short query with empty history is the first turn, not an ellipsis."""
        assert needs_coref("CS101", []) is False

    def test_pronoun_in_middle_of_sentence_fires(self):
        """Pronoun anywhere in the query, not just at the start."""
        assert needs_coref(
            "Can you give me a list of those courses?",
            _PRIOR_TURN,
        ) is True

    def test_what_about_pattern(self):
        assert needs_coref("What about Spring 2026?", _PRIOR_TURN) is True

    def test_history_can_be_objects_not_dicts(self):
        """needs_coref accepts any iterable of opaque history entries —
        the gate doesn't read content, only length."""

        class FakeMsg:
            content = "..."

        assert needs_coref("its prereq?", [FakeMsg()]) is True
