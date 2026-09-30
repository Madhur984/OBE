"""
Build `app/data/aktu_catalogue.json` from AKTU's published syllabus PDFs.

    python tools/build_aktu_catalogue.py <folder-of-pdfs>

The folder is fetched by `tools/fetch_aktu_syllabi.py`, which reads the
university's own year-by-year index pages. Neither script runs at request
time -- the catalogue is a build artefact, checked in, and the tool is
offline as always.

What gets kept, and why the filters are strict:

  * A course needs a title, a code and at least two outcomes. An untitled
    entry reaches a workbook as "Untitled (BEC601)" and is worse than absent.
  * Outcomes that are not outcomes are dropped. A numbered reference list
    parses as a numbered outcome list -- one AKTU document yields a "course"
    whose first outcome is "V. Sivaramakrishna (Ed.), Cultural Heritage of
    India, Bharatiya Vidya Bhavan, Mumbai" -- and a bibliography scored
    against Programme Outcomes is nonsense that looks like data.
  * The same course appears in several years' documents. The newest wins,
    and the year it came from is recorded so a user can see a 2021 syllabus
    standing in for a 2026 course and refuse it.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.aktu_catalogue import CATALOGUE_PATH, code_key, normalise_title
from app.engine.conventions import classify_course
from app.engine.syllabus_parser import parse_upload

MIN_COS = 2
MAX_SYLLABUS_CHARS = 8000
# Below this a "syllabus" is a stray heading, not course content.
MIN_SYLLABUS_CHARS = 200
# AKTU writes every B.Tech course in five units. Six is a rounding of that;
# ten or fifteen is one course's unit table running into its neighbour's,
# which happens where two syllabi share a page. The outcomes of such an entry
# are still its own and are kept -- only the merged syllabus is dropped,
# because content from the wrong course is worse than no content.
MAX_UNITS = 6

# A statement that is really a bibliography entry, a table row or a heading.
# Each of these was found in the corpus, not imagined.
_NOT_AN_OUTCOME = (
    # "V. Sivaramakrishna (Ed.), Cultural Heritage of India-Course Material,
    # Bharatiya Vidya Bhavan, Mumbai." -- publisher lists, comma-heavy, and
    # they never contain a verb the student performs.
    re.compile(r"\b(ed\.|eds\.|publish|press|pvt|ltd|vol\.|reprint|"
               r"edition|isbn|new delhi|prentice|mcgraw|wiley|springer|"
               r"pearson|oxford university|cambridge university)\b", re.I),
    # "Sharma, R. K., Introduction to ..." -- an author-initial run.
    re.compile(r"^[A-Z][a-z]+,\s*[A-Z]\.\s*[A-Z]?\.?,"),
    # Evaluation-scheme and credit rows that survived the table flattening.
    re.compile(r"^\s*\d+\s*[-:]\s*\d+\s*[-:]\s*\d+\s*$"),
    re.compile(r"\b(sessional|end semester|total marks|credits?\s*:\s*\d)\b", re.I),
)
_HAS_LETTERS = re.compile(r"[A-Za-z]{3,}")


def is_outcome(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 20 or len(t) > 400:
        return False
    if not _HAS_LETTERS.search(t):
        return False
    if any(p.search(t) for p in _NOT_AN_OUTCOME):
        return False
    # A bibliography entry is mostly commas and proper nouns; a course outcome
    # is a sentence. Four or more commas in a statement with no lower-case
    # verb-looking word is the shape that survives every filter above.
    if t.count(",") >= 4 and not re.search(r"\b(to|the|and|of|will|able)\b", t.lower()):
        return False
    return True


def year_of(name: str) -> str:
    m = re.match(r"(20\d\d)", name)
    return m.group(1) if m else ""


def build(folder: Path) -> dict:
    by_key: dict[tuple, dict] = {}
    seen = defaultdict(int)
    read = kept = dropped_cos = merged = labs = 0

    for pdf in sorted(folder.glob("*.pdf")):
        try:
            parsed = parse_upload(pdf.name, pdf.read_bytes())
        except Exception as exc:                       # noqa: BLE001
            print(f"  !! {pdf.name[:60]}: {type(exc).__name__} {exc}")
            continue
        year = year_of(pdf.name)
        for course in parsed.courses:
            read += 1
            title = (course.title or "").strip()
            code = (course.code or "").strip()
            if not title or not code:
                continue
            cos = [c.statement.strip() for c in course.cos
                   if is_outcome(c.statement)]
            dropped_cos += len(course.cos) - len(cos)
            units = list(course.units)
            syllabus = ("" if len(units) > MAX_UNITS
                        else "\n".join(u.topics for u in units)[:MAX_SYLLABUS_CHARS])
            if len(units) > MAX_UNITS:
                merged += 1
            # A lab has no unit table; its experiment list is its syllabus,
            # and it is the only place a lab's tools are named.
            if not syllabus and course.experiments:
                syllabus = course.experiments[:MAX_SYLLABUS_CHARS]
                labs += 1
            # Two outcomes, or a real syllabus. Either is enough, because this
            # catalogue exists for the syllabus -- AKTU publishes exactly one
            # outcome for BNC502 Essence of Indian Traditional Knowledge
            # ("Ability to understand, connect up and explain basics of Indian
            # Traditional knowledge") and five modules of content behind it,
            # and it is the content the department cannot get anywhere else.
            if len(cos) < MIN_COS and len(syllabus) < MIN_SYLLABUS_CHARS:
                continue
            key = (code_key(code), normalise_title(title))
            seen[key] += 1
            prev = by_key.get(key)
            # Newest wins; between two of the same year, the one that carries
            # unit content wins -- an entry with outcomes but no syllabus is
            # exactly the gap this catalogue exists to close.
            better = (prev is None or year > prev["year"]
                      or (year == prev["year"] and len(syllabus) > len(prev["syllabus"])))
            if not better:
                continue
            by_key[key] = {
                "code": code, "title": title,
                "kind": classify_course(title=title, code=code, ltp=course.ltp),
                "cos": cos, "syllabus": syllabus,
                "source": pdf.name, "year": year,
            }

    kept = len(by_key)
    courses = sorted(by_key.values(), key=lambda c: (c["code"], c["title"]))
    print(f"read {read} parsed courses -> kept {kept} distinct "
          f"({read - kept} were untitled, thin, or a duplicate of a newer year)")
    print(f"dropped {dropped_cos} statements that were not course outcomes")
    print(f"dropped the syllabus of {merged} courses whose unit table ran into "
          f"a neighbour's (more than {MAX_UNITS} units)")
    with_syl = sum(1 for c in courses if c["syllabus"])
    print(f"{with_syl} of {kept} carry course content "
          f"({labs} of them a lab's experiment list rather than a unit table)")
    return {
        "source": "Dr. A.P.J. Abdul Kalam Technical University, published B.Tech syllabi",
        "url": "https://aktu.ac.in/syllabus.html",
        "courses": courses,
    }


def main() -> None:
    folder = Path(sys.argv[1] if len(sys.argv) > 1
                  else r"d:/Hokage X Pirate king/OBE/aktu_corpus")
    if not folder.is_dir():
        raise SystemExit(f"not a folder: {folder}")
    data = build(folder)
    CATALOGUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOGUE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    size = CATALOGUE_PATH.stat().st_size
    print(f"wrote {CATALOGUE_PATH} ({size // 1024} KB)")


if __name__ == "__main__":
    main()
