"""
Question -> CO tagging tests.

The deterministic scorer is tested against realistic exam questions for three
different courses. The LLM arbitration path is tested with a stub client: the
live API call is deliberately NOT exercised here, because a test suite that
needs a network and a paid key is a test suite people stop running.
"""
from __future__ import annotations

import pytest

from app.engine import qco_tagger
from app.engine.qco_tagger import (
    QuestionTag, arbitrate_with_llm, llm_status, score_question,
    split_question_paper, tag_paper, tag_paper_smart, tag_question,
)
from app.seed.curriculum import BY_CODE


def cos_for(code: str) -> dict[str, tuple[str, list[int]]]:
    return {co.code: (co.statement, co.blooms_levels) for co in BY_CODE[code].cos}


class TestSplitPaper:
    def test_numbered_questions(self):
        qs = split_question_paper("1. Explain X.\n2. Analyze Y.\n3. Design Z.")
        assert [q[0] for q in qs] == ["Q1", "Q2", "Q3"]

    def test_q_prefixed(self):
        qs = split_question_paper("Q1 Explain the architecture.\nQ2 Compare paging.")
        assert [q[0] for q in qs] == ["Q1", "Q2"]

    def test_sub_parts_inherit_the_number(self):
        qs = split_question_paper(
            "1 (a) Explain the schema architecture.\n(b) Draw the diagram for it."
        )
        assert [q[0] for q in qs] == ["Q1a", "Q1b"]

    def test_strips_trailing_mark_annotation(self):
        qs = split_question_paper("1. Explain normalization in databases. [10 marks]")
        assert "marks" not in qs[0][1]

    def test_unnumbered_text_is_one_question(self):
        qs = split_question_paper("Explain the three-schema architecture.")
        assert len(qs) == 1 and qs[0][0] == "Q1"

    def test_empty_input(self):
        assert split_question_paper("   ") == []


class TestDeterministicTagging:
    DBMS = [
        ("Explain the three-schema architecture of a DBMS with a neat diagram.", "CO1"),
        ("Write SQL queries to find the second highest salary from an employee table.", "CO3"),
        ("A schedule S has transactions T1 and T2. Determine whether S is conflict "
         "serializable.", "CO4"),
        ("Compare two-phase locking with timestamp ordering for concurrency control.", "CO5"),
    ]
    OS = [
        ("Explain the functions and structure of an operating system.", "CO1"),
        ("Compute average waiting time using SJF scheduling for the given process set.", "CO2"),
        ("Apply the bankers algorithm to determine whether the system is in a safe "
         "state.", "CO3"),
        ("Compare paging with segmentation in terms of fragmentation.", "CO4"),
        ("Analyze SCAN and C-SCAN disk scheduling for the given request queue.", "CO5"),
    ]
    COMMS = [
        ("Explain the block diagram of a digital communication system.", "CO1"),
        ("Apply the sampling theorem to determine the Nyquist rate for the given "
         "signal.", "CO2"),
        ("Analyze the bit error rate performance of BPSK over an AWGN channel.", "CO3"),
        ("Design a digital communication link meeting a specified BER and bandwidth "
         "budget.", "CO4"),
        ("Evaluate the coding gain of a convolutional code for the given channel.", "CO5"),
    ]

    @pytest.mark.parametrize("text,expected", DBMS)
    def test_dbms(self, text, expected):
        assert tag_question("Q", text, cos_for("KCS501")).co_code == expected

    @pytest.mark.parametrize("text,expected", OS)
    def test_operating_systems(self, text, expected):
        assert tag_question("Q", text, cos_for("KCS502")).co_code == expected

    @pytest.mark.parametrize("text,expected", COMMS)
    def test_digital_communication(self, text, expected):
        assert tag_question("Q", text, cos_for("KEC601")).co_code == expected

    def test_acronyms_expand_to_match_spelled_out_outcomes(self):
        """A question says "ER diagram"; the outcome says "entity-relationship"."""
        scores = score_question(
            "Draw an ER diagram and convert it to a relational schema.",
            cos_for("KCS501"),
        )
        by_code = {s.co_code: s.score for s in scores}
        assert by_code["CO2"] > 0, "ER should register against the ER-modelling outcome"

    def test_ambiguous_question_is_flagged_not_guessed_confidently(self):
        """
        This question genuinely straddles CO1 and CO2. The scorer may pick
        either, but it must not claim high confidence -- a confidently wrong
        tag silently corrupts attainment, while a flagged one gets reviewed.
        """
        t = tag_question(
            "Q2",
            "Draw an ER diagram for a library management system and convert it to "
            "relational schema.",
            cos_for("KCS501"),
        )
        assert t.needs_review

    def test_unrelated_question_is_unsure(self):
        t = tag_question("Q9", "Discuss the weather in Lucknow today.", cos_for("KCS501"))
        assert t.confidence == "unsure"
        assert t.co_code is None

    def test_empty_question(self):
        t = tag_question("Q1", "   ", cos_for("KCS501"))
        assert t.co_code is None and t.confidence == "unsure"

    def test_rationale_names_the_matching_terms(self):
        t = tag_question("Q1", "Write SQL queries to fetch employee records.",
                         cos_for("KCS501"))
        assert "sql" in t.rationale.lower() or "queries" in t.rationale.lower()

    def test_scores_cover_every_outcome(self):
        scores = score_question("Explain normalization.", cos_for("KCS501"))
        assert {s.co_code for s in scores} == set(cos_for("KCS501"))

    def test_is_deterministic(self):
        q = "Analyze the bit error rate performance of BPSK."
        a = tag_question("Q", q, cos_for("KEC601"))
        b = tag_question("Q", q, cos_for("KEC601"))
        assert (a.co_code, a.confidence, a.scores[0].score) == \
               (b.co_code, b.confidence, b.scores[0].score)

    def test_whole_paper_accuracy(self):
        """Overall accuracy across three courses, with ambiguity allowed to abstain."""
        cases = [("KCS501", self.DBMS), ("KCS502", self.OS), ("KEC601", self.COMMS)]
        correct = total = 0
        for course, items in cases:
            cos = cos_for(course)
            for text, expected in items:
                total += 1
                correct += tag_question("Q", text, cos).co_code == expected
        assert correct == total, f"{correct}/{total} tagged correctly"


class TestPaperLevel:
    def test_tag_paper_returns_one_tag_per_question(self):
        qs = split_question_paper(
            "1. Explain the architecture of a DBMS.\n"
            "2. Write SQL queries for the employee table.\n"
        )
        tags = tag_paper(qs, cos_for("KCS501"))
        assert len(tags) == 2
        assert all(isinstance(t, QuestionTag) for t in tags)

    def test_method_is_deterministic_without_llm(self):
        tags = tag_paper([("Q1", "Explain the architecture of a DBMS.")],
                         cos_for("KCS501"))
        assert tags[0].method == "deterministic"
        assert tags[0].model is None


class _StubMessages:
    def __init__(self, payload: str, raises: Exception | None = None):
        self.payload, self.raises = payload, raises
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises

        class _Block:
            type = "text"
            text = self.payload

        class _Resp:
            content = [_Block()]

        return _Resp()


class _StubAnthropic:
    def __init__(self, payload: str, raises: Exception | None = None):
        self.messages = _StubMessages(payload, raises)

    def __call__(self, *a, **k):
        return self


@pytest.fixture
def stub_llm(monkeypatch):
    """Make the LLM path callable without a network or an API key."""
    def _install(payload: str, raises: Exception | None = None):
        stub = _StubAnthropic(payload, raises)
        module = type("_M", (), {"Anthropic": lambda *a, **k: stub})
        monkeypatch.setitem(__import__("sys").modules, "anthropic", module)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        return stub
    return _install


class TestLLMArbitration:
    """
    The live API call is not exercised here -- only the logic around it:
    parsing, validation, and every failure path. That logic is where the bugs
    that matter live.
    """
    AMBIGUOUS = ("Q2", "Draw an ER diagram for a library management system and "
                 "convert it to relational schema.")

    def _base_tag(self):
        return tag_question(*self.AMBIGUOUS, cos_for("KCS501"))

    def test_accepts_a_valid_answer(self, stub_llm):
        stub_llm('{"co_code":"CO2","confidence":"high","reason":"ER modelling to schema."}')
        t = arbitrate_with_llm(self._base_tag(), cos_for("KCS501"))
        assert t.co_code == "CO2"
        assert t.method == "llm-arbitrated"
        assert t.confidence == "high"
        assert t.rationale == "ER modelling to schema."

    def test_records_the_model_for_reproducibility(self, stub_llm):
        stub_llm('{"co_code":"CO2","confidence":"high","reason":"x"}')
        t = arbitrate_with_llm(self._base_tag(), cos_for("KCS501"), model="pinned-model")
        assert t.model == "pinned-model"

    def test_rejects_a_hallucinated_outcome_code(self, stub_llm):
        """A code outside the course must never be accepted."""
        stub_llm('{"co_code":"CO99","confidence":"high","reason":"invented"}')
        base = self._base_tag()
        t = arbitrate_with_llm(base, cos_for("KCS501"))
        assert t.co_code != "CO99"
        assert t.method == "deterministic"

    def test_null_answer_is_allowed(self, stub_llm):
        stub_llm('{"co_code":null,"confidence":"low","reason":"none fit"}')
        t = arbitrate_with_llm(self._base_tag(), cos_for("KCS501"))
        assert t.co_code is None
        assert t.confidence == "unsure"

    def test_prose_around_the_json_is_tolerated(self, stub_llm):
        stub_llm('Here is my answer:\n{"co_code":"CO2","confidence":"medium","reason":"ok"}\n')
        t = arbitrate_with_llm(self._base_tag(), cos_for("KCS501"))
        assert t.co_code == "CO2" and t.confidence == "review"

    def test_malformed_reply_falls_back(self, stub_llm):
        stub_llm("I could not decide.")
        base = self._base_tag()
        t = arbitrate_with_llm(base, cos_for("KCS501"))
        assert t.method == "deterministic"
        assert t.co_code == base.co_code

    def test_api_error_falls_back_instead_of_raising(self, stub_llm):
        stub_llm("", raises=RuntimeError("connection reset"))
        t = arbitrate_with_llm(self._base_tag(), cos_for("KCS501"))
        assert t.method == "deterministic"

    def test_temperature_zero_and_model_are_sent(self, stub_llm):
        stub = stub_llm('{"co_code":"CO2","confidence":"high","reason":"x"}')
        arbitrate_with_llm(self._base_tag(), cos_for("KCS501"))
        sent = stub.messages.calls[0]
        assert sent["temperature"] == 0
        assert sent["model"]

    def test_prompt_lists_only_this_courses_outcomes(self, stub_llm):
        stub = stub_llm('{"co_code":"CO2","confidence":"high","reason":"x"}')
        arbitrate_with_llm(self._base_tag(), cos_for("KCS501"))
        prompt = stub.messages.calls[0]["messages"][0]["content"]
        for code in cos_for("KCS501"):
            assert code in prompt

    def test_no_key_means_no_call(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        base = self._base_tag()
        t = arbitrate_with_llm(base, cos_for("KCS501"))
        assert t.method == "deterministic"

    def test_smart_tagging_escalates_only_uncertain_questions(self, stub_llm):
        stub = stub_llm('{"co_code":"CO2","confidence":"high","reason":"x"}')
        qs = [
            ("Q1", "Write SQL queries to fetch employee records."),   # unambiguous
            self.AMBIGUOUS,                                          # straddles CO1/CO2
        ]
        confident, uncertain = tag_paper(qs, cos_for("KCS501"))
        assert not confident.needs_review and uncertain.needs_review, (
            "fixture no longer exercises the escalation path"
        )

        tags = tag_paper_smart(qs, cos_for("KCS501"))
        # Only the uncertain one costs a call; escalating settled questions
        # would swap reproducible answers for model-dependent ones.
        assert len(stub.messages.calls) == 1
        assert tags[0].method == "deterministic"
        assert tags[1].method == "llm-arbitrated"

    def test_use_llm_false_disables_escalation(self, stub_llm):
        stub = stub_llm('{"co_code":"CO2","confidence":"high","reason":"x"}')
        tag_paper_smart([self.AMBIGUOUS], cos_for("KCS501"), use_llm=False)
        assert stub.messages.calls == []


class TestLLMStatus:
    def test_reports_missing_key_clearly(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        s = llm_status()
        assert s["available"] is False
        assert "ANTHROPIC_API_KEY" in s["detail"]

    def test_reports_ready_when_configured(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        monkeypatch.setattr(qco_tagger, "llm_available", lambda: True)
        s = qco_tagger.llm_status()
        assert s["api_key_set"] is True
