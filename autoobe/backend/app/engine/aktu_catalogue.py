"""
Every course AKTU publishes, so nobody has to upload a syllabus twice.

The department's Universal Human Values course reached 13 of the 20 cells
their sheet fills from its outcome text alone, and all 20 once its syllabus
was read. That was not a one-off: unit content is what tells a course on the
Constitution of India apart from one on Antenna Theory when both outcomes
begin "Understand the...". But it only helped the one course whose PDF
happened to be uploaded, and asking a department to find and upload forty
syllabus PDFs is asking them to do the work twice.

So the syllabi ship with the tool. `app/data/aktu_catalogue.json` holds every
B.Tech course AKTU published between 2021-22 and 2026-27 -- code, title,
outcomes, unit-wise content -- built by `tools/build_aktu_catalogue.py` from
the university's own PDFs. A course in a workbook is looked up here by code
and title, and its unit content joins the mapping the same way an uploaded
one would.

Two things this is NOT:

  * It is not a source of course outcomes. A department's COs are their own,
    often deliberately rewritten from the university's -- their C202.3 reads
    "Analyse the value of harmonious relationship" where AKTU publishes
    "Understand the value of ...". We map what they wrote. The catalogue's
    own outcomes are here for reference and for the corpus tests, never to
    silently replace a department's.
  * It is not authority. A match is reported to the user with the document it
    came from and the year, because a 2021 syllabus standing in for a 2026
    course is a judgement they may want to refuse.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "data" / "aktu_catalogue.json"

# Words that carry no identity. "Lab" is handled separately -- it is the one
# word whose presence or absence must never be ignored, because a lab and its
# theory course otherwise share every word of their titles.
_STOP = frozenset(
    "and the of for in to a an with its on using by".split())
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
          "vi": "6", "vii": "7", "viii": "8"}
# The series letter changes between AKTU's code revisions -- KVE301 became
# BVE301, KEC302 became BEC302 -- while the subject letters and the number
# stay put. Dropping that leading letter is what survives the revision.
_CODE = re.compile(r"\b([A-Z]{2,4})\s*-?\s*(\d{3})([A-Z]?)\b")

# What departments write on a workbook against what the university prints on
# the syllabus. An acronym shares no words with its expansion, so no amount of
# fuzzy matching finds it -- and these are the courses whose syllabus content
# matters most, because their outcomes are the ones that read alike.
#
# Kept short and specific on purpose: each of these appears in a real
# workbook. It is not a place to guess at abbreviations nobody uses.
_ALIAS = {
    "uhvpe": "universal human values and professional ethics",
    "uhv": "universal human values",
    "hss": "humanities and social sciences",
    "pme": "project management and entrepreneurship",
    "ds": "data structures",
    "oops": "object oriented programming",
    "dbms": "database management systems",
    "dsp": "digital signal processing",
    "vlsi": "vlsi design",
    "iot": "internet of things",
    "tc": "technical communication",
}

MIN_SCORE = 0.7


@dataclass(frozen=True)
class CatalogueCourse:
    code: str
    title: str
    kind: str
    cos: tuple[str, ...]
    syllabus: str
    source: str                     # the AKTU document this was read from
    year: str

    @property
    def units(self) -> int:
        return self.syllabus.count("\n") + 1 if self.syllabus else 0

    def to_dict(self) -> dict:
        return {"code": self.code, "title": self.title, "kind": self.kind,
                "cos": list(self.cos), "syllabus": self.syllabus,
                "source": self.source, "year": self.year,
                "units": self.units}


def normalise_title(title: str) -> str:
    """A title reduced to the words that identify it.

    Roman numerals become digits so "MATHEMATICS-III" and "Mathematics 3"
    are the same course, and "&" becomes "and" before the stop list eats it.
    A title that is only an acronym is expanded first -- "UHVPE" and
    "Universal Human Values and Professional Ethics" are one course, and
    nothing about the strings says so.
    """
    t = (title or "").lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9 ]+", " ", t).strip()
    t = _ALIAS.get(t, t)
    return " ".join(_ROMAN.get(w, w) for w in t.split() if w not in _STOP)


def code_key(text: str) -> str:
    """A course code with its series letter dropped: KVE-301 and BVE301 -> VE301.

    AKTU renumbers by changing the leading letter and nothing else, so this is
    what identifies a course across revisions. Codes are unique within the
    university, which is why an exact match here carries the identification
    on its own when the title is an acronym or a shorthand.
    """
    m = _CODE.search((text or "").upper())
    if not m:
        return ""
    letters = m.group(1)
    if len(letters) == 3 and letters[0] in "BK":
        letters = letters[1:]
    return letters + m.group(2) + m.group(3)


def _is_lab(text: str) -> bool:
    return bool(re.search(r"\blab(oratory)?\b", (text or ""), re.I))


@lru_cache(maxsize=1)
def load_catalogue(path: str | Path | None = None) -> tuple[CatalogueCourse, ...]:
    p = Path(path or CATALOGUE_PATH)
    if not p.exists():
        return ()
    raw = json.loads(p.read_text(encoding="utf-8"))
    return tuple(
        CatalogueCourse(code=c.get("code", ""), title=c.get("title", ""),
                        kind=c.get("kind", ""), cos=tuple(c.get("cos") or ()),
                        syllabus=c.get("syllabus", ""),
                        source=c.get("source", ""), year=c.get("year", ""))
        for c in raw.get("courses", []))


def score(title: str, code: str, course: CatalogueCourse) -> float:
    """How well a workbook course matches a catalogue one, 0 to about 1.3.

    Both titles have to be substantially the same, not one contained in the
    other. Containment was tried first, on the reasoning that AKTU titles a
    course more fully than a department does -- "Rural Development" against
    "Rural Development: Administration and Planning". It also matched a
    department's "Cyber Security" to AKTU's "Introduction to Security of
    Cyber-Physical Systems", which is a different course entirely, and handed
    it that course's syllabus.

    So the shorter title no longer wins by being short: the words that only
    one side has count against the match. What rescues the genuine case is
    the code, which is what a code is for.
    """
    want = set(normalise_title(title).split())
    have = set(normalise_title(course.title).split())
    want.discard("lab"); have.discard("lab")
    base = (len(want & have) / max(len(want), len(have))
            if want and have else 0.0)

    key = code_key(f"{code} {title}")
    if key and code_key(course.code) == key:
        # An exact code carries the identification by itself, because AKTU's
        # codes are unique per course. The title still moves the score, so a
        # full agreement outranks a code that matches and a title that does
        # not -- but "UHVPE / KVE-301" finds BVE301 with no shared word.
        s = max(base, 0.75) + 0.25 * base
    else:
        s = base
    # A lab and its theory course share every word of their titles and must
    # never stand in for each other -- their outcomes and units are different.
    if _is_lab(f"{title} {code}") != _is_lab(course.title):
        s -= 0.4
    return s


def find_course(*, title: str = "", code: str = "",
                catalogue: tuple[CatalogueCourse, ...] | None = None,
                min_score: float = MIN_SCORE) -> tuple[CatalogueCourse | None, float]:
    """The best AKTU course for this title and code, or nothing.

    Returns the score too, so a caller can say how sure it is rather than
    presenting a guess as a fact.
    """
    best, top = None, 0.0
    for c in catalogue if catalogue is not None else load_catalogue():
        s = score(title, code, c)
        if s > top or (s == top and best is not None and c.year > best.year):
            best, top = c, s
    if best is None or top < min_score:
        return None, round(top, 2)
    return best, round(top, 2)


def syllabus_for(*, title: str = "", code: str = "") -> tuple[str, dict]:
    """The unit-wise content AKTU publishes for this course, and its provenance.

    The provenance travels with the text on purpose. A 2021 syllabus standing
    in for a 2026 course is a judgement the user may want to refuse, and they
    can only refuse what they can see.
    """
    course, s = find_course(title=title, code=code)
    if course is None or not course.syllabus:
        return "", {}
    return course.syllabus, {
        "code": course.code, "title": course.title, "year": course.year,
        "source": course.source, "units": course.units, "score": s,
    }
