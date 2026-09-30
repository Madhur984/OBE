"""
Measuring the question -> CO tagger.

"14 of 14 on realistic questions" was a demo, not evidence. It says nothing
about what happens on the questions the tagger finds hard, and it cannot
detect a regression, which is the thing that actually bites: a tokenizer
tweak that fixes one course and quietly breaks another.

The metric that matters here is not accuracy. It is the **silent error
rate** -- questions the tagger marked high-confidence and got wrong. A
question flagged for review costs a faculty member ten seconds. A question
confidently mistagged goes into the attainment arithmetic and moves a SAR
number that nobody will ever re-check. The two failures differ by orders of
magnitude in cost, so they are reported separately and the suite holds a
floor on the dangerous one specifically.

Coverage is reported alongside, because the degenerate way to drive silent
errors to zero is to flag everything for review, and that is not a product.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.engine.qco_tagger import tag_paper

CORPUS_PATH = Path(__file__).resolve().parents[2] / "tests" / "eval" / "qco_corpus.json"


@dataclass(frozen=True)
class LabelledQuestion:
    course_code: str
    question_no: str
    text: str
    gold_co: str
    difficulty: str = "normal"        # normal | hard | ambiguous


@dataclass(frozen=True)
class CourseFixture:
    code: str
    title: str
    cos: dict[str, tuple[str, list[int]]]
    questions: list[LabelledQuestion]
    split: str = "dev"


@dataclass
class ClassMetrics:
    co_code: str
    support: int = 0          # gold occurrences
    predicted: int = 0
    correct: int = 0

    @property
    def precision(self) -> float:
        return self.correct / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return self.correct / self.support if self.support else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return (2 * p * r / (p + r)) if (p + r) else 0.0


@dataclass
class EvalResult:
    total: int = 0
    tagged: int = 0                       # a CO was proposed at all
    high_confidence: int = 0
    correct: int = 0
    correct_high: int = 0
    silent_errors: list[dict] = field(default_factory=list)   # high confidence, wrong
    flagged_errors: list[dict] = field(default_factory=list)  # wrong but flagged
    untagged: list[dict] = field(default_factory=list)
    per_co: dict[str, ClassMetrics] = field(default_factory=dict)
    confusion: dict[str, int] = field(default_factory=dict)
    per_course: dict[str, dict] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def coverage(self) -> float:
        """Share answered without asking a human."""
        return self.high_confidence / self.total if self.total else 0.0

    @property
    def silent_error_rate(self) -> float:
        """Wrong *and* asserted confidently -- the number that matters."""
        return len(self.silent_errors) / self.total if self.total else 0.0

    @property
    def precision_at_high(self) -> float:
        """Accuracy restricted to what the tagger claimed to be sure about."""
        return self.correct_high / self.high_confidence if self.high_confidence else 0.0

    @property
    def macro_f1(self) -> float:
        if not self.per_co:
            return 0.0
        return sum(m.f1 for m in self.per_co.values()) / len(self.per_co)

    def summary(self) -> str:
        lines = [
            f"Questions            {self.total}",
            f"Accuracy             {self.accuracy:.1%}  ({self.correct}/{self.total})",
            f"Coverage (high conf) {self.coverage:.1%}  ({self.high_confidence}/{self.total})",
            f"Precision @ high     {self.precision_at_high:.1%}  "
            f"({self.correct_high}/{self.high_confidence})",
            f"Silent error rate    {self.silent_error_rate:.1%}  "
            f"({len(self.silent_errors)} confidently wrong)",
            f"Macro F1             {self.macro_f1:.3f}",
        ]
        if self.confusion:
            lines.append("")
            lines.append("Most confused pairs:")
            for pair, n in sorted(self.confusion.items(), key=lambda kv: -kv[1])[:6]:
                lines.append(f"  {pair}: {n}")
        if self.silent_errors:
            lines.append("")
            lines.append("Confidently wrong:")
            for e in self.silent_errors[:8]:
                lines.append(
                    f"  [{e['course']}] {e['question_no']} gold={e['gold']} "
                    f"got={e['predicted']} — {e['text'][:64]}"
                )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "accuracy": round(self.accuracy, 4),
            "coverage": round(self.coverage, 4),
            "precision_at_high": round(self.precision_at_high, 4),
            "silent_error_rate": round(self.silent_error_rate, 4),
            "macro_f1": round(self.macro_f1, 4),
            "silent_errors": self.silent_errors,
            "flagged_errors": self.flagged_errors,
            "untagged": self.untagged,
            "confusion": self.confusion,
            "per_course": self.per_course,
            "per_co": {
                k: {"support": m.support, "predicted": m.predicted,
                    "precision": round(m.precision, 3), "recall": round(m.recall, 3),
                    "f1": round(m.f1, 3)}
                for k, m in sorted(self.per_co.items())
            },
        }


def load_corpus(path: Path | str | None = None, *,
                split: str | None = None) -> list[CourseFixture]:
    """
    Load the corpus, optionally restricted to one split.

    `split="holdout"` is the number to quote. Tuning the scorer while reading
    holdout failures turns the whole exercise into fitting the test set, so
    the discipline is: inspect dev, report holdout.
    """
    p = Path(path) if path else CORPUS_PATH
    raw = json.loads(p.read_text(encoding="utf-8"))
    fixtures: list[CourseFixture] = []
    for c in raw["courses"]:
        if split and c.get("split", "dev") != split:
            continue
        cos = {code: (v["statement"], v["blooms"]) for code, v in c["cos"].items()}
        questions = [
            LabelledQuestion(
                course_code=c["code"], question_no=q["question_no"], text=q["text"],
                gold_co=q["co"], difficulty=q.get("difficulty", "normal"),
            )
            for q in c["questions"]
        ]
        fixtures.append(CourseFixture(c["code"], c["title"], cos, questions,
                                      c.get("split", "dev")))
    return fixtures


def evaluate(fixtures: list[CourseFixture] | None = None, *,
             split: str | None = None) -> EvalResult:
    """
    Run the deterministic tagger over the corpus. No LLM: this measures the
    layer that runs for everyone, with no key and no network, and it must
    stay reproducible run to run or it cannot detect a regression.
    """
    fixtures = fixtures or load_corpus(split=split)
    result = EvalResult()
    per_co: dict[str, ClassMetrics] = defaultdict(lambda: ClassMetrics(""))

    for fx in fixtures:
        tags = tag_paper(
            [(q.question_no, q.text) for q in fx.questions],
            cos=fx.cos,
        )
        by_no = {t.question_no: t for t in tags}
        course_total = course_correct = course_high = 0

        for q in fx.questions:
            key = f"{fx.code}:{q.gold_co}"
            m = per_co[key]
            if not m.co_code:
                per_co[key] = m = ClassMetrics(key)
            m.support += 1

            tag = by_no.get(q.question_no)
            result.total += 1
            course_total += 1
            predicted = tag.co_code if tag else None
            high = bool(tag and tag.confidence == "high")

            if predicted is None:
                result.untagged.append({
                    "course": fx.code, "question_no": q.question_no,
                    "gold": q.gold_co, "text": q.text, "difficulty": q.difficulty,
                })
                continue

            result.tagged += 1
            pkey = f"{fx.code}:{predicted}"
            pm = per_co[pkey]
            if not pm.co_code:
                per_co[pkey] = pm = ClassMetrics(pkey)
            pm.predicted += 1

            if high:
                result.high_confidence += 1
                course_high += 1

            if predicted == q.gold_co:
                result.correct += 1
                course_correct += 1
                pm.correct += 1
                if high:
                    result.correct_high += 1
            else:
                pair = f"{fx.code}:{q.gold_co}→{predicted}"
                result.confusion[pair] = result.confusion.get(pair, 0) + 1
                record = {
                    "course": fx.code, "question_no": q.question_no,
                    "gold": q.gold_co, "predicted": predicted,
                    "confidence": tag.confidence, "text": q.text,
                    "difficulty": q.difficulty,
                }
                (result.silent_errors if high else result.flagged_errors).append(record)

        result.per_course[fx.code] = {
            "questions": course_total,
            "accuracy": round(course_correct / course_total, 3) if course_total else 0.0,
            "coverage": round(course_high / course_total, 3) if course_total else 0.0,
        }

    result.per_co = dict(per_co)
    return result


def main() -> None:                                    # pragma: no cover
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    show_dev = "--dev" in sys.argv
    for split in ("holdout", "dev"):
        if split == "dev" and not show_dev:
            continue
        print(f"=== {split.upper()} ===")
        print(evaluate(split=split).summary())
        print()
    if not show_dev:
        print("(pass --dev to inspect the tuning split)")


if __name__ == "__main__":                             # pragma: no cover
    main()
