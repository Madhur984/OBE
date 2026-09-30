"""
Automatic course-outcome detection from a real syllabus document.

Every AKTU B.Tech syllabus carries a "Course Outcome (CO) | Bloom's Knowledge
Level (KL)" table for each course -- verified by reading three current
syllabus PDFs directly. That table is the highest-quality CO source available,
so a college should never retype it.

Handles the shapes those documents actually use:

    CO-1 : Students will be able to perform destructive testing.   K2
    CO1   Explain need, types and tools of Virtualization.         K3, K4
    | CO2 | Analyze the effect of heat treatment on hardness. | K4 |
    CO3: Design digital communication systems. (K6)

Multi-valued K-levels are the norm in recently revised syllabi, not an edge
case -- over 50 instances were found across two current AKTU documents -- so
the parser always returns a list.

DOCX tables are read structurally (near-perfect); PDFs go through text
extraction, which is messier, so every parse reports a confidence and the UI
shows the result for review rather than silently trusting it.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

# "CO1", "CO-1", "CO 1", "CO.1", "C01" (a common OCR/typing slip for CO1)
_CO_MARKER = re.compile(r"^\s*(?:CO|C0)\s*[-–—.:]?\s*(\d{1,2})\b\s*[:.\-–—)]?\s*", re.I)
# A K-level group anywhere: "K2", "K3,K4", "K2, K3 and K4", "(K6)"
_K_GROUP = re.compile(r"\(?\bK\s*([1-6])(?:\s*[,/&and\s]+\s*K?\s*([1-6]))?"
                      r"(?:\s*[,/&and\s]+\s*K?\s*([1-6]))?\b\)?", re.I)
_K_TRAILING = re.compile(r"[\s|,;·—–-]*\(?\bK\s*[1-6]\b[^A-Za-z]*$", re.I)
_BLOOM_WORD = re.compile(
    r"\b(remember(ing)?|understand(ing)?|apply(ing)?|analyz(e|ing)|analys(e|ing)|"
    r"evaluat(e|ing)|creat(e|ing))\b", re.I)
_BLOOM_WORD_LEVEL = {
    "remember": 1, "understand": 2, "apply": 3,
    "analyz": 4, "analys": 4, "evaluat": 5, "creat": 6,
}
_COURSE_CODE = re.compile(
    r"(?:course|subject|paper)\s*code\s*[:\-]?\s*([A-Z]{1,4}[\s-]?\d{2,4}[A-Z]?)", re.I)
_BARE_CODE = re.compile(r"\b([A-Z]{2,4}\s?\d{3}[A-Z]?)\b")
_COURSE_TITLE = re.compile(
    r"(?:course|subject|paper)\s*(?:title|name)\s*[:\-]?\s*([^\n|]{3,80})", re.I)
_NOISE_LINE = re.compile(
    r"^\s*(course outcome|co\b.*\bkl\b|bloom|knowledge level|s\.?\s*no|sr\.?\s*no|"
    r"unit\s+[ivx\d]|total|page\s*\d|table\s+no)", re.I)
_LTP = re.compile(r"\b([0-9])\s*[-–:]\s*([0-9])\s*[-–:]\s*([0-9])\b")

# The other way AKTU writes outcomes, and the more common one in the newer
# schemes: a heading, then a plain numbered list with no "CO" prefix and no
# K-levels at all.
#
#     Course Outcomes: At the end of this course students will demonstrate
#     the ability to:
#     1. Express the concept of VLSI design and CMOS circuits.
#     2. Analyze mathematical methods and circuit analysis models.
#
# This cannot be recognised by the numbering alone, because a syllabus is
# full of numbered lists -- text books, reference books, lab experiments. So
# the list is only read after the heading, and only until the next section.
_CO_HEADING = re.compile(
    r"^\s*course\s+outcomes?\s*[:\-–]?\s*(?:at\s+the\s+end|on\s+completion|"
    r"after\s+(?:completion|studying)|students?\b|upon\b|.{0,80})?\s*$", re.I)
_NUMBERED_ITEM = re.compile(r"^\s*(\d{1,2})\s*[.)\]]\s+(.*)$")
# A bibliography heading in any of the spellings AKTU actually uses. The
# section terminators below know "Text Books" and "Reference Books" and knew
# neither "Suggested Text & Reference Books" nor "Suggested Textbooks /
# Reference Books" -- 169 headings across the corpus, each one a numbered
# reference list left running as if it were a numbered outcome list. That is
# how a course on Indian tradition came to have twelve outcomes, the first of
# which was "V. Sivaramakrishna (Ed.), Cultural Heritage of India, Bharatiya
# Vidya Bhavan, Mumbai".
#
# Anchored to the end of the line on purpose. Without the `$` this matches
# "Reading comprehension and listening skills", which is a unit topic in the
# Technical Communication syllabus, not a heading.
_BOOKS_HEADING = re.compile(
    r"^\s*(?:suggested|recommended|prescribed)?\s*"
    r"(?:text\s*(?:books?)?\s*(?:[&/]|and)?\s*)?"
    r"(?:reference\s*)?(?:books?|readings?)\s*[:\-–—]?\s*$", re.I)
# Anything that ends a course-outcome list.
_SECTION_END = re.compile(
    r"^\s*(text\s*book|reference\s*book|suggested\s*read|unit\s|course\s+content|"
    r"syllabus|list\s+of\s+experiment|suggestive\s+list|note\s*:|pre-?requisite|"
    r"evaluation\s+scheme|department(al)?\s+elective|open\s+elective)", re.I)
# Running headers and footers that interleave with a wrapped list. These must
# be skipped rather than treated as the end of it: in a real AKTU PDF, CO4 and
# CO5 of a five-outcome list sit on the far side of a page break.
_PAGE_FURNITURE = re.compile(
    r"^\s*(curriculum\s*&?\s*evaluation|page\s*\|?\s*\d+|\d+\s*\|\s*page|"
    r"dr\.?\s*a\.?\s*p\.?\s*j\.?|b\.?\s*tech\.?\s+(fourth|third|second|first)\s+year)"
    r"|page\s+\d+\s*$", re.I)

MIN_STATEMENT_WORDS = 3
MAX_STATEMENT_CHARS = 400
# A numbered list of outcomes that runs past this is something else -- a list
# of experiments, or references that slipped through the section guard.
MAX_NUMBERED_OUTCOMES = 12

# ---- the unit-wise course content -------------------------------------
#
# Every AKTU course block carries its syllabus above the outcomes, in a table
# that opens with the same three column headings in all four of the syllabus
# PDFs checked ("Unit Topics Lectures", "Unit Topic Lectures"):
#
#     Unit Topics Lectures
#     I  Logic simplification and combinational logic design: Number Systems...
#     8
#     II Combinational circuits: Analysis and Design of combinational...
#     8
#
# This is the only place a document says what the course actually teaches. A
# course outcome says "Design various ADCs and DACs"; the unit behind it names
# switched-capacitor circuits, dual-slope and successive approximation, and
# Unit IV names Verilog RTL and the EDA tools by vendor. That is real evidence
# for the tool-usage PO that no outcome statement carries, so it is read here
# and offered to the scorer as corroboration -- see `competency.propose_for_co`.
_UNIT_TABLE_HEADER = re.compile(r"^\s*unit\s+topics?\s*(?:lectures?)?\s*$", re.I)
# "I", "IV", "Unit-III", "UNIT V" -- Roman only. Arabic numerals are not safe
# here: a bare "3" opening a line is far more often a lecture count or a
# reference-list number than a unit.
_UNIT_ROW = re.compile(r"^\s*(?:unit\s*[-–—.:]?\s*)?(I{1,3}|IV|VI{0,3}|IX|X)\b"
                       r"\s*[-–—.:)]?\s+(.{6,})$", re.I)
_UNIT_LECTURES = re.compile(r"^\s*\d{1,2}\s*$")
# The other layout, and the one the non-technical courses use: no table at
# all, just a heading per unit followed by prose.
#
#     UNIT-1 Introduction to Value Education
#     Understanding the need, basic guidelines, content, and process for
#     Value Education, Self-Exploration ...
#
# Missing this is not cosmetic. The UHV syllabus is five units of human
# values whose fifth is titled "Implications ... on Professional Ethics", and
# with no units read there was nothing to evidence the ethics column of an
# ethics course. Requires the literal word UNIT, so it cannot fire on the
# bare Roman numerals a running text contains.
# "Module" is the same thing under another name, and 241 headings across 18 of
# AKTU's documents use it -- including both non-credit papers, whose content is
# written "Module 1-Introduction and Basic Information about Indian
# Constitution:". Requiring the literal word UNIT left those courses with no
# content at all, which is the same failure the UHV syllabus had.
_UNIT_HEADING = re.compile(
    r"^\s*(?:unit|module)\s*[-–—.:]?\s*(\d{1,2}|[IVX]{1,4})\b\s*[-–—.:)]?\s*(.*)$", re.I)
# What ends the unit table. "Course outcome" is included and deliberately
# falls through to the CO reader rather than being swallowed here.
_UNIT_END = re.compile(
    r"^\s*(text\s*/?\s*reference\s*books?|reference\s*books?|text\s*books?|"
    r"suggested\s*read|course\s+outcomes?|note\s*:|suggestive\s+list|"
    r"list\s+of\s+experiments?|pre-?requisite|evaluation\s+scheme)", re.I)
MAX_UNIT_CHARS = 2000

_ROMAN_UNIT = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5,
               "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}


def _unit_ordinal(number: str) -> int:
    """Where this unit sits in its course's sequence, Roman or Arabic.

    Used for one thing: noticing that the sequence has restarted. A course
    whose units run I, II, III, IV, V and then 1, 2, 3 is not a course with
    eight units -- it is two courses whose boundary the header reader missed,
    and the second one's content piling onto the first. Attributing a Go
    Programming module to Adaptive Signal Processing is worse than losing it,
    so the restart ends the list.
    """
    n = (number or "").strip().upper()
    if n.isdigit():
        return int(n)
    return _ROMAN_UNIT.get(n, 0)

# A laboratory course has no unit table -- it has a list of experiments, and
# that list is its syllabus. Not reading it left every lab in the corpus with
# no course content at all, which matters more for a lab than for a lecture
# course: the two POs a lab most obviously serves are Conduct Investigations
# and Modern Tool Usage, and neither is evidenced by an outcome reading
# "Understand working of PN junction diode". The experiment list behind that
# one names a CRO, a multimeter, a function generator, and the graphical
# measurement of static and dynamic resistance.
#
# It is course content and is offered to the scorer as exactly that --
# corroboration, capped below the partial threshold, unable to map a PO on its
# own. It is emphatically NOT a list of outcomes: "Write a program to
# demonstrate the working of the ID3 algorithm" is a task a student performs
# in one session, not a statement of what the course achieves, and reading
# those as outcomes is a defect this parser has elsewhere.
_EXPERIMENTS_HEADER = re.compile(
    r"^\s*(?:suggestive\s+)?list\s+of\s+(?:experiments?|practicals?)\b|"
    r"^\s*(?:lab(?:oratory)?|practical)\s+(?:experiments?|list|work)\b|"
    r"^\s*experiments?\s*:\s*$", re.I)
_EXPERIMENTS_END = re.compile(
    r"^\s*(text\s*/?\s*reference\s*books?|reference\s*books?|text\s*books?|"
    r"suggested\s*read|course\s+outcomes?|pre-?requisite|evaluation\s+scheme|"
    r"lab\s+outcomes?)", re.I)
MAX_EXPERIMENT_CHARS = 2500


@dataclass
class ParsedCO:
    code: str
    statement: str
    blooms_levels: list[int]
    source: str = "text"          # "docx-table" | "pdf-table" | "pdf-text" | "text"
    raw: str = ""
    # True once `infer_missing_bloom_levels` has supplied the level from the
    # verb. `has_bloom` cannot carry this: inference fills `blooms_levels` in
    # place, so afterwards every outcome looks declared. Faculty have to be
    # able to see which levels the university tagged and which we guessed --
    # a guessed K feeds CL, and CL decides the mapping level under Rule 2.
    bloom_inferred: bool = False

    @property
    def has_bloom(self) -> bool:
        return bool(self.blooms_levels)

    @property
    def bloom_declared(self) -> bool:
        """The syllabus itself tagged this level."""
        return bool(self.blooms_levels) and not self.bloom_inferred


@dataclass
class ParsedUnit:
    """One row of a course's unit-wise syllabus table."""
    number: str                   # "I", "II", ... as the document writes it
    topics: str

    @property
    def title(self) -> str:
        """The bolded lead-in before the colon, where the document has one."""
        head = self.topics.split(":", 1)[0]
        return head.strip() if 0 < len(head) <= 90 else ""


@dataclass
class ParsedCourse:
    """
    One course found in a syllabus document. AKTU publishes a whole semester
    per PDF, so a single upload usually contains a dozen courses -- each with
    its own CO1..CO5. They must stay separated, or CO1 from Databases merges
    with CO1 from Networks.
    """
    code: str | None
    title: str | None
    cos: list[ParsedCO] = field(default_factory=list)
    ltp: tuple[int, int, int] | None = None
    units: list[ParsedUnit] = field(default_factory=list)
    # A lab's syllabus, which is a list of experiments rather than a unit
    # table. Held apart from `units` because it is a different shape and the
    # user is shown a different sentence about it.
    experiments: str = ""

    @property
    def label(self) -> str:
        if self.code and self.title:
            return f"{self.code} — {self.title}"
        return self.code or self.title or "Untitled course"

    @property
    def syllabus_text(self) -> str:
        """
        The unit topics as one block, for the scorer to read as corroboration.

        Kept separate from the outcome statements on purpose: this is evidence
        about the course, not about any one outcome, and the scorer weights it
        accordingly.

        A lab contributes its experiment list here instead of a unit table.
        Both are the same kind of claim -- what this course actually covers --
        and the scorer treats them identically.
        """
        parts = [u.topics for u in self.units]
        if self.experiments:
            parts.append(self.experiments)
        return " ".join(parts)


@dataclass
class ParsedSyllabus:
    courses: list[ParsedCourse] = field(default_factory=list)
    source: str = "text"
    warnings: list[str] = field(default_factory=list)

    @property
    def cos(self) -> list[ParsedCO]:
        """Flattened view: the COs of the first course found."""
        return self.courses[0].cos if self.courses else []

    @property
    def course_code(self) -> str | None:
        return self.courses[0].code if self.courses else None

    @property
    def course_title(self) -> str | None:
        return self.courses[0].title if self.courses else None

    @property
    def ltp(self) -> tuple[int, int, int] | None:
        return self.courses[0].ltp if self.courses else None

    @property
    def total_cos(self) -> int:
        return sum(len(c.cos) for c in self.courses)

    @property
    def confidence(self) -> str:
        """
        How much the result should be trusted before review. Structural DOCX
        table reads are near-perfect; PDF text extraction is not.
        """
        cos = [c for course in self.courses for c in course.cos]
        if not cos:
            return "none"
        with_bloom = sum(1 for c in cos if c.has_bloom)
        if self.source == "docx-table" and with_bloom == len(cos):
            return "high"
        if with_bloom >= max(1, len(cos) * 2 // 3):
            return "medium"
        return "low"


def _clean(text: str) -> str:
    text = text.replace(" ", " ").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def _extract_k_levels(text: str) -> list[int]:
    """
    K-levels the *university declared*, and only those.

    A word-based fallback used to run here, scanning the whole statement for
    any Bloom word. That reads subject matter as the action -- "Express the
    concept of VLSI design" came back K6 because of the phrase "VLSI design"
    -- and it also made a guess indistinguishable from a declaration, so
    `has_bloom` claimed the syllabus had tagged a level when nothing had.
    Inference now lives in `infer_missing_bloom_levels`, where it reads the
    leading verb and is labelled as inferred.
    """
    levels: list[int] = []
    for m in _K_GROUP.finditer(text):
        for g in m.groups():
            if g:
                levels.append(int(g))
    return sorted(set(levels))


def _strip_k_levels(text: str) -> str:
    """
    Remove the trailing K-level so it does not end up inside the statement.

    The trailing-separator sweep runs only when a K-level was actually
    removed, or when the line carries a table pipe. A wrapped outcome ends
    its line on a real comma -- "Massive MIMO," / "beamforming, and mmWave" --
    and stripping that unconditionally silently rewrote the statement.
    """
    out = _K_TRAILING.sub("", text).strip()
    removed = out != text.strip()
    if _K_GROUP.search(out) and len(out) < 60:
        out = _K_GROUP.sub("", out)
        removed = True
    if removed or "|" in text:
        out = re.sub(r"[\s|,;:·—–-]+$", "", out)
    else:
        out = re.sub(r"[\s|;·]+$", "", out)
    return out.strip()


def _looks_like_statement(text: str) -> bool:
    if len(text) < 12 or len(text) > MAX_STATEMENT_CHARS:
        return False
    if len(text.split()) < MIN_STATEMENT_WORDS:
        return False
    return bool(re.search(r"[a-z]{3}", text))


# A course header: "Database Management System (BCS501)" or "BCS501 Networks".
_COURSE_HEADER_TITLE_FIRST = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9 ,&/'()+.-]{4,70}?)\s*\(\s*([A-Z]{2,4}\s?\d{3}[A-Z]?)\s*\)\s*$")
# The optional second code is how AKTU prints a paper taught in either of two
# semesters -- "BNC501/ BNC601- CONSTITUTION OF INDIA", "BCS753/ BCS851
# Project". Without it the line matched nothing and both of the department's
# non-credit courses arrived untitled. The first code is kept, because that is
# the one their workbook carries.
#
# The hyphen inside the code is the other half: "BCT-051 Yarn Technology-III"
# is written that way throughout the textile syllabi.
_COURSE_HEADER_CODE_FIRST = re.compile(
    r"^\s*([A-Z]{2,4}\s?-?\s?\d{3}[A-Z]?)"
    r"(?:\s*/\s*[A-Z]{2,4}\s?-?\s?\d{3}[A-Z]?)?"
    r"\s*[:\-–—]?\s*([A-Za-z][A-Za-z0-9 ,&/'()+.-]{4,70})\s*$")
# The AKTU per-course banner, which carries the L-T-P as well:
#     BEC701 VLSI Design 3L:0T:0P 3 Credits
#     BEC074 Drone Systems: Design, Control & Navigation 3L:0T:0P 3
# The title is taken as whatever sits between the code and the L-T-P rather
# than by a character class, because titles contain colons, ampersands and
# commas, and excluding any of those loses real courses.
_COURSE_HEADER_LTP = re.compile(
    r"^\s*([A-Z]{2,4}\s?-?\s?\d{2,4}[A-Z]?)\s+(.+?)\s*"
    r"(\d)\s*L\s*[:/]\s*(\d)\s*T\s*[:/]\s*(\d)\s*P\b", re.I)
# A run of bare CO labels, e.g. "CO 2 CO 3 CO 4" — that is a CO-PO matrix
# header row, never an outcome statement.
_CO_LABEL_RUN = re.compile(r"^(?:\s*(?:CO|C0)\s*[-–.]?\s*\d{1,2}\s*[,|]?\s*){2,}$", re.I)
_ALL_CAPS_BANNER = re.compile(r"^[A-Z0-9 ,.&()/'-]{12,}$")
# A line that opens with a course code is a course header even when the rest
# of it is upper case. Plenty of schemes print titles in caps -- "BEC073
# MICROWAVE AND RADAR ENGINEERING" -- and the banner guard was swallowing the
# title while keeping the code, which is how a course reaches the UI with a
# code but no name. A real banner ("DEPARTMENT OF ELECTRONICS...") has no
# code, and the three-digit tail keeps years out: "AKTU 2026" is not a code.
#
# The optional second code has to be here too, or the guard reads
# "BNC501/ BNC601- CONSTITUTION OF INDIA" as a department banner and throws
# the whole header away -- which is why both of the department's non-credit
# courses arrived untitled even after the header pattern learned to read them.
_LEADING_COURSE_CODE = re.compile(
    r"^\s*[A-Z]{2,4}\s?-?\s?\d{3}[A-Z]?"
    r"(?:\s*/\s*[A-Z]{2,4}\s?-?\s?\d{3}[A-Z]?)?\s*[-–—:]?\s+[A-Z]")


def _looks_like_course_title(line: str) -> bool:
    """
    Is this line a course name sitting above its code, or something else?

    Everything the parser recognises as structure is excluded first, then what
    is left has to read like a name: two to twelve words, mostly letters, and
    no trailing colon (which marks a section label -- "Objectives:").
    """
    text = (line or "").strip().rstrip(".")
    if not (4 <= len(text) <= 80) or text.endswith(":"):
        return False
    words = text.split()
    if not (2 <= len(words) <= 12):
        return False
    for pattern in (_CO_MARKER, _NUMBERED_ITEM, _CO_HEADING, _SECTION_END,
                    _UNIT_HEADING, _UNIT_TABLE_HEADER, _NOISE_LINE,
                    _PAGE_FURNITURE, _CO_LABEL_RUN):
        if pattern.match(text):
            return False
    letters = sum(c.isalpha() for c in text)
    return letters >= len(text) * 0.7 and _course_header(text) is None


def _course_header(line: str) -> tuple[str, str, tuple[int, int, int] | None] | None:
    """Return (code, title, ltp) when the line introduces a new course."""
    if (_ALL_CAPS_BANNER.match(line) and "(" not in line
            and not _LEADING_COURSE_CODE.match(line)):
        return None                        # university/department banner

    m = _COURSE_HEADER_LTP.match(line)
    if m and not _CO_MARKER.match(line):
        title = _clean_course_title(m.group(2))
        if title and not re.match(r"^(syllabus|semester|branch|unit|total)\b", title, re.I):
            return (
                re.sub(r"[\s-]", "", m.group(1)).upper(),
                title,
                (int(m.group(3)), int(m.group(4)), int(m.group(5))),
            )

    m = _COURSE_HEADER_TITLE_FIRST.match(line)
    if m:
        return (re.sub(r"\s", "", m.group(2)).upper(),
                _clean_course_title(m.group(1)), None)
    m = _COURSE_HEADER_CODE_FIRST.match(line)
    if m and not _CO_MARKER.match(line):
        title = _clean_course_title(m.group(2))
        if not re.match(r"^(syllabus|semester|branch|unit)\b", title, re.I):
            return re.sub(r"\s", "", m.group(1)).upper(), title, None
    return None


# Junk that trails a course title on a header line: the credits, the L-T-P in
# any of its spellings, and the bare column numbers left behind when a table
# row is flattened to text. Extracting the L-T-P separately is not enough --
# the characters stay in the title and travel all the way into the downloaded
# document, so "Wireless and Mobile Communication 3-0-0 3 Credits" ends up as
# the course name.
_TITLE_TAIL = re.compile(
    r"(?:"
    r"\s*\|?\s*\d+(?:\.\d+)?\s*credits?\b.*"
    r"|\s*\|?\s*\d\s*L\s*[:/-]\s*\d\s*T\s*[:/-]\s*\d\s*P\b.*"
    r"|\s*\|?\s*\d\s*[-–:]\s*\d\s*[-–:]\s*\d\b.*"
    r"|\s*\|(?:\s*\d+\s*\|?)+\s*"
    r"|\s+\d+(?:\s+\d+)+\s*"
    r")$", re.I)
_TITLE_LABEL = re.compile(
    r"^\s*(?:course|subject|paper)\s*(?:title|name|code)\s*[:\-–]?\s*", re.I)


def _clean_course_title(title: str) -> str:
    """
    A course name, with the header furniture taken off the end.

    Applied repeatedly because a real header stacks them: "... 3L:0T:0P 3
    Credits" is two tails, and a flattened table row is a third.
    """
    title = _TITLE_LABEL.sub("", _clean(title))
    for _ in range(4):
        stripped = _TITLE_TAIL.sub("", title).strip(" .:|-–")
        if stripped == title:
            break
        title = stripped
    return title.strip(" .:|-–")


# A "recover the course name from the line above the outcomes" rescue used to
# live here. It is deliberately gone. On the three real AKTU schemes it fired
# five times and was wrong five times -- it adopted "P. K. Champati Ray,
# Wiley, 2022", "Edition, Penram International Publication", "VHDL PROGRAMS"
# and the running page header as course names, because in a real scheme the
# lines directly above "Course Outcomes:" are the reference list, not a title.
# A wrong course name is worse than none: it reaches the SAR looking authored.
# The header reflow above is what actually recovers these courses, and where
# it cannot, the UI says "no header" and shows the block's first outcome.


def _finalise(co: ParsedCO | None) -> ParsedCO | None:
    if co is None:
        return None
    statement = _strip_k_levels(co.statement).strip()
    if _CO_LABEL_RUN.match(statement) or not _looks_like_statement(statement):
        return None
    co.statement = statement.rstrip(" .") + "."
    return co


# How many times a line has to repeat before it is furniture rather than
# content. Three is enough to separate a page header from an outcome that
# happens to appear in two courses.
RUNNING_HEADER_REPEATS = 3
MIN_RUNNING_HEADER_CHARS = 12


def _running_headers(lines: list[str]) -> set[str]:
    """
    Lines the document repeats on page after page.

    "ELECTRONICS AND COMMUNICATION ENGINEERING" sits at the top of all thirty
    pages of a real AKTU scheme. It is not matched by the page-furniture
    patterns -- it is a department name, not a page number -- so it was being
    appended to whichever outcome was open when the page turned. One course's
    CO5 read "...various Testing Methodologies. ELECTRONICS AND COMMUNICATION
    ENGINEERING." and that text went on to feed the CO -> PO mapping.

    Numbered items and CO rows are excluded from the count: two courses in the
    same file legitimately share an outcome, and no amount of repetition makes
    a real outcome into furniture.
    """
    counts: dict[str, int] = {}
    for raw in lines:
        line = raw.strip()
        if len(line) < MIN_RUNNING_HEADER_CHARS:
            continue
        if _NUMBERED_ITEM.match(line) or _CO_MARKER.match(line):
            continue
        if _CO_HEADING.match(line) or _course_header(line):
            continue
        counts[line] = counts.get(line, 0) + 1
    return {l for l, n in counts.items() if n >= RUNNING_HEADER_REPEATS}


def extract_courses_from_text(text: str, *, source: str = "text") -> list[ParsedCourse]:
    """
    Pull per-course CO tables out of flowing text.

    Written against what PDF extraction of a real AKTU syllabus actually
    produces: blank lines between every row, K-levels on their own line after
    the statement, a CO code sometimes alone on its line with the statement
    following, and a dozen courses in one file.
    """
    lines = [l.rstrip() for l in text.splitlines()]
    running = _running_headers(lines)
    courses: list[ParsedCourse] = []
    current_course = ParsedCourse(code=None, title=None)
    current: ParsedCO | None = None
    seen: dict[str, ParsedCO] = {}

    def close_co() -> None:
        """
        Commit the outcome being built. A CO code that has already appeared in
        this block means a course header went unrecognised -- these documents
        hold a dozen courses each, all numbering from CO1. Starting a new
        course is far safer than merging, which silently fuses one course's
        outcome with another's and is invisible in the output.
        """
        nonlocal current
        done = _finalise(current)
        current = None
        if done is None:
            return
        if done.code in seen:
            close_course(inherit=True)
        seen[done.code] = done
        current_course.cos.append(done)

    # The unit-wise syllabus table, read between its header and the first of
    # "Text/Reference Books" or "Course outcomes".
    in_units = False
    unit_no: str | None = None
    unit_buf: list[str] = []
    # The last unit ordinal recorded for the course being read, and whether it
    # was written in Roman or Arabic, so a restart can be told from a
    # continuation. Both reset with the course.
    last_unit_ord = 0
    last_unit_roman: bool | None = None

    def unit_restarts(number: str) -> bool:
        """True when this heading opens a sequence rather than continuing one.

        Two signals, and the second is the one that matters. A number that
        does not advance is the obvious restart. A change of *notation* is the
        reliable one: AKTU writes a course's units one way throughout, so
        units running I, II, III, IV, V and then 6, 7, 8 are two courses, not
        one course with eight units -- which is exactly how eight modules of
        Go Programming came to be filed under Adaptive Signal Processing.
        """
        n = (number or "").strip()
        roman = not n.isdigit()
        if last_unit_roman is not None and roman != last_unit_roman:
            return True
        o = _unit_ordinal(number)
        return bool(last_unit_ord) and (o == 0 or o <= last_unit_ord)
    in_experiments = False
    exp_buf: list[str] = []

    def close_experiments() -> None:
        nonlocal in_experiments, exp_buf
        if exp_buf and current_course is not None and not current_course.experiments:
            current_course.experiments = _clean(" ".join(exp_buf))[:MAX_EXPERIMENT_CHARS]
        in_experiments, exp_buf = False, []

    def close_unit() -> None:
        nonlocal unit_no, unit_buf, last_unit_ord, last_unit_roman
        if unit_no and unit_buf:
            topics = _clean(" ".join(unit_buf))[:MAX_UNIT_CHARS]
            if len(topics) >= 12:
                current_course.units.append(ParsedUnit(unit_no.upper(), topics))
                last_unit_ord = _unit_ordinal(unit_no) or last_unit_ord
                last_unit_roman = not unit_no.strip().isdigit()
        unit_no, unit_buf = None, []

    def close_units() -> None:
        nonlocal in_units
        close_unit()
        in_units = False
        close_experiments()

    def close_course(inherit: bool = False) -> None:
        """
        Commit the course block. `inherit` is set when the split was triggered
        by a repeated CO code rather than a real header: the following block
        has no header of its own, so it is left unnamed for the user to label
        rather than given the previous course's identity.
        """
        nonlocal current_course, seen, last_unit_ord, last_unit_roman
        close_units()
        if not inherit:
            close_co()
        # A named course with real content but no outcomes is still a
        # course. AKTU publishes BNC502 Essence of Indian Traditional
        # Knowledge with five modules of content and a single outcome written
        # as running prose rather than a list -- so requiring outcomes threw
        # the whole course away, content and all, and the department's Indian
        # Tradition block had nothing behind it. The user sees an empty CO
        # table and writes their own, which is what they do for that course
        # anyway.
        if current_course.cos or (
                current_course.code and current_course.title
                and (current_course.units or current_course.experiments)):
            current_course.cos.sort(key=lambda c: int(c.code[2:]))
            courses.append(current_course)
        current_course = ParsedCourse(code=None, title=None)
        seen = {}
        last_unit_ord, last_unit_roman = 0, None

    # True while inside a "Course Outcomes:" heading's numbered list.
    in_numbered_list = False
    last_number = 0
    # The last non-empty line seen, so a title printed above its course code
    # can still be recovered.
    prev_line = ""

    for raw_line in lines:
        line = _clean(raw_line.replace("|", " "))
        if not line:
            continue                       # blanks separate every row; not a boundary

        # Running headers and footers interleave with wrapped outcomes and
        # must not end the list -- CO4 and CO5 often sit past a page break.
        if _PAGE_FURNITURE.match(line):
            continue

        # Remembered before anything consumes this line, and used only by the
        # course-header branch below.
        previous, prev_line = prev_line, line

        header = _course_header(line)
        if header:
            close_course()
            in_numbered_list, last_number = False, 0
            current_course.code, current_course.title, ltp = header
            if ltp is not None:
                current_course.ltp = ltp
            # Some schemes print the title on its own line ABOVE the code:
            #
            #     Universal Human Values and Professional Ethics
            #     BVE301 / BVE401/        L T P C
            #
            # so the header matches a code with no title and the course
            # reaches the workbook as "Untitled". The line just above is the
            # only place the name exists, and it is taken only when it looks
            # like a name and like nothing else the parser recognises.
            if not current_course.title and _looks_like_course_title(previous):
                current_course.title = _clean_course_title(previous)
            continue

        # A running header repeats on every page and is not part of any
        # outcome. Checked after the header and heading tests so a real course
        # banner or a repeated "Course Outcomes:" line is never mistaken for
        # furniture.
        # The unit table opens every course block, so in a whole-semester PDF
        # its header repeats a dozen times and `running` classifies it as page
        # furniture. It has to be recognised before that test, not after.
        if _UNIT_TABLE_HEADER.match(line):
            close_co()
            close_units()
            in_units = True
            continue

        # An explicit "UNIT-3 Understanding Harmony ..." heading opens a unit
        # whether or not a table header was ever seen, and closes the previous
        # one. Checked before the running-header filter for the same reason
        # the table header is: in a whole-semester file it repeats per course.
        uh = _UNIT_HEADING.match(line)
        if uh and not in_numbered_list:
            close_co()
            close_unit()
            if unit_restarts(uh.group(1)):
                # A second sequence inside one course block: the boundary
                # between two courses that the header reader missed. Ending
                # the list loses the second course's content; extending it
                # files that content under the first course, which is worse.
                close_units()
                continue
            in_units = True
            unit_no, unit_buf = uh.group(1), ([uh.group(2)] if uh.group(2).strip() else [])
            continue

        # A lab's syllabus opens here. Checked before the running-header
        # filter for the same reason the unit-table header is: in a
        # whole-semester PDF it repeats once per lab.
        if _EXPERIMENTS_HEADER.match(line):
            close_co()
            close_units()
            in_experiments = True
            continue

        if line.strip() in running:
            continue

        # ---- the experiment list, which is a lab's course content --------
        if in_experiments:
            if _EXPERIMENTS_END.match(line) or _BOOKS_HEADING.match(line):
                # Falls through, so "Course Outcomes:" both ends the
                # experiment list and opens the CO list below.
                close_experiments()
            else:
                exp_buf.append(line)
                continue

        # ---- the unit-wise syllabus table ----------------------------
        if in_units:
            if _UNIT_END.match(line) or _BOOKS_HEADING.match(line):
                # Falls through on purpose: "Course outcomes:" both ends the
                # unit table and opens the CO list, and only the reader below
                # can start that list.
                close_units()
            else:
                um = _UNIT_ROW.match(line)
                if um:
                    close_unit()
                    if unit_restarts(um.group(1)):
                        close_units()
                        continue
                    unit_no, unit_buf = um.group(1), [um.group(2)]
                elif _UNIT_LECTURES.match(line):
                    close_unit()          # the lecture count closes its row
                elif unit_no:
                    unit_buf.append(line)
                continue

        if _CO_HEADING.match(line):
            # A second "Course Outcomes:" heading means a second course. The
            # boundary has to be drawn HERE, not later when the repeated CO1
            # is spotted: anything between the two headings -- including the
            # next course's title -- would otherwise be filed under the
            # previous course, which is how a recovered title landed on the
            # wrong card.
            if current_course.cos:
                close_course()
            close_co()
            in_numbered_list, last_number = True, 0
            continue


        # A line that labels itself "CO2" is a new outcome whatever list state
        # we think we are in, so it leaves the numbered-list state and is read
        # by the CO reader further down.
        #
        # Without this the list-continuation rules ate it. A document that
        # writes "Course Outcomes:" and then "CO1 ...", "CO2 ..." -- rather
        # than a bare numbered list -- opens the numbered-list state on the
        # heading; CO1 falls through it and is read; and CO2 then meets the
        # terminal-punctuation rule, which sees CO1's finished sentence,
        # closes the list and swallows the line. The course comes out with
        # outcomes numbered CO1, CO3, CO4 -- **393 of the 1,455 courses in
        # AKTU's published syllabi, 27% of the corpus**, each quietly missing
        # its second outcome and nothing anywhere saying so.
        #
        # The terminal-punctuation rule itself is right and stays: it is what
        # stopped the last item of a list running on into the section below
        # it, which had cost a professional-ethics course the only outcome it
        # had containing the word "ethical".
        if in_numbered_list and _CO_MARKER.match(line) and not _CO_LABEL_RUN.match(line):
            in_numbered_list = False

        if in_numbered_list:
            if _SECTION_END.match(line) or _BOOKS_HEADING.match(line):
                close_co()
                in_numbered_list = False
                continue
            nm = _NUMBERED_ITEM.match(line)
            if nm:
                number, rest = int(nm.group(1)), nm.group(2).strip()
                # Only a list that counts 1, 2, 3... is an outcome list. A jump
                # means the numbering belongs to something else and the list
                # has already ended.
                if number == last_number + 1 and number <= MAX_NUMBERED_OUTCOMES:
                    close_co()
                    last_number = number
                    current = ParsedCO(
                        code=f"CO{number}",
                        statement=_strip_k_levels(rest),
                        blooms_levels=_extract_k_levels(rest),
                        source=source,
                        raw=line,
                    )
                    continue
                close_co()
                in_numbered_list = False
                continue
            # Not numbered and not a section end: a wrapped continuation --
            # unless the outcome is already a finished sentence.
            #
            # The last item of a list has nothing after it to stop it, so it
            # ran on into whatever section followed and was then thrown away
            # for exceeding MAX_STATEMENT_CHARS. In the UHV syllabus that lost
            # CO5, "Distinguish between ethical and unethical practices" --
            # the only outcome in a professional-ethics course containing the
            # word "ethical", and with it the whole ethics column.
            #
            # Terminal punctuation is the signal: PDF extraction breaks lines
            # mid-phrase, so a genuine continuation almost never follows a
            # full stop, and a heading almost always does.
            if current is not None and current.statement.rstrip().endswith((".", "?", "!")):
                close_co()
                in_numbered_list = False
                continue
            if current is not None and len(current.statement) < MAX_STATEMENT_CHARS:
                current.statement = _clean(
                    f"{current.statement} {_strip_k_levels(line)}").strip()
                continue

        m = _CO_MARKER.match(line)
        if m and not _CO_LABEL_RUN.match(line):
            close_co()
            rest = line[m.end():].strip()
            current = ParsedCO(
                code=f"CO{int(m.group(1))}",
                statement=_strip_k_levels(rest),
                blooms_levels=_extract_k_levels(rest),
                source=source,
                raw=line,
            )
            continue

        if current is None:
            if current_course.ltp is None:
                lm = _LTP.search(line)
                if lm:
                    current_course.ltp = (int(lm.group(1)), int(lm.group(2)), int(lm.group(3)))
            continue

        if _CO_LABEL_RUN.match(line) or _NOISE_LINE.match(line):
            close_co()
            continue

        levels = _extract_k_levels(line)
        stripped = _strip_k_levels(line)
        if levels and not stripped:
            # A K-level alone on its line belongs to the outcome above it and
            # marks its end -- the standard AKTU layout.
            current.blooms_levels = sorted(set(current.blooms_levels + levels))
            close_co()
            continue
        if len(current.statement) < MAX_STATEMENT_CHARS:
            # Continuation text only. K-levels are deliberately NOT harvested
            # here: a "K3" inside a wrapped statement is usually bleed from a
            # neighbouring table cell, and collecting it produced outcomes
            # tagged with four different Bloom levels.
            current.statement = _clean(f"{current.statement} {stripped}").strip()

    close_course()
    return [c for c in courses if c.cos]


def extract_cos_from_text(text: str, *, source: str = "text") -> list[ParsedCO]:
    """Backwards-compatible flat view: the COs of the first course found."""
    courses = extract_courses_from_text(text, source=source)
    return courses[0].cos if courses else []


def _cos_from_docx_tables(document) -> list[ParsedCO]:
    """
    Read CO/KL tables structurally. This is the reliable path: a real table
    keeps the statement and its K-level in separate cells, so nothing has to
    be inferred from layout.
    """
    out: list[ParsedCO] = []
    for table in document.tables:
        rows = [[_clean(c.text) for c in r.cells] for r in table.rows]
        if not rows:
            continue
        header = " ".join(rows[0]).lower()
        table_is_cos = ("course outcome" in header or re.search(r"\bco\b", header)) \
            and ("kl" in header or "knowledge level" in header or "bloom" in header)

        for row in rows:
            cells = [c for c in row if c]
            if len(cells) < 2:
                continue
            joined = " ".join(cells)
            m = _CO_MARKER.match(cells[0]) or _CO_MARKER.match(joined)
            if not m and not table_is_cos:
                continue

            code = f"CO{int(m.group(1))}" if m else None
            # Statement: the longest cell that is not the code or the K-level.
            candidates = [
                c for c in cells
                if not _CO_MARKER.fullmatch(c.strip())
                and not re.fullmatch(r"\(?K\s*[1-6][\s,/&aKnd0-9]*\)?", c.strip(), re.I)
            ]
            statement = max(candidates, key=len) if candidates else ""
            statement = _strip_k_levels(_CO_MARKER.sub("", statement))
            levels = _extract_k_levels(joined)

            if code and _looks_like_statement(statement):
                out.append(ParsedCO(code, statement.rstrip(" .") + ".",
                                    levels, "docx-table", joined))

    best: dict[str, ParsedCO] = {}
    for co in out:
        prev = best.get(co.code)
        if prev is None or len(co.statement) > len(prev.statement):
            best[co.code] = co
    return [best[k] for k in sorted(best, key=lambda c: int(c[2:]))]


def _metadata(text: str) -> tuple[str | None, str | None, tuple[int, int, int] | None]:
    code = title = None
    ltp = None
    m = _COURSE_CODE.search(text)
    if m:
        code = re.sub(r"\s|-", "", m.group(1)).upper()
    else:
        m = _BARE_CODE.search(text[:2000])
        if m:
            code = re.sub(r"\s", "", m.group(1)).upper()
    m = _COURSE_TITLE.search(text)
    if m:
        candidate = _clean(m.group(1))
        candidate = re.sub(r"\s*(course\s*code|credits?|l\s*-?\s*t\s*-?\s*p).*$", "",
                           candidate, flags=re.I).strip()
        if 3 <= len(candidate) <= 80:
            title = candidate
    m = _LTP.search(text[:3000])
    if m:
        ltp = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return code, title, ltp


def _backfill_metadata(courses: list[ParsedCourse], text: str) -> list[ParsedCourse]:
    """
    Fill in code/title for a block that had no recognisable header -- typically
    a single-course document where the code sits in a title block rather than
    directly above the CO table. Only applied when the document holds one
    course, since guessing across a multi-course file would mislabel every one.
    """
    if len(courses) != 1:
        return courses
    code, title, ltp = _metadata(text)
    c = courses[0]
    c.code = c.code or code
    c.title = c.title or title
    c.ltp = c.ltp or ltp
    if not c.title and c.code:
        c.title = _title_above_code(text, c.code)
    return courses


def _title_above_code(text: str, code: str) -> str:
    """
    The course name printed on the line above its code, with no header line
    the parser can match:

        Universal Human Values and Professional Ethics
        BVE301 / BVE401/        L T P C

    Here the code line is a run of codes and column letters, so no course
    header matches it at all and the name is only recoverable by position.
    Without this the course reaches the workbook as "Untitled".
    """
    key = re.escape(code.replace(" ", "")).replace(r"\-", r"[\s-]?")
    lines = [_clean(l) for l in text.splitlines()]
    for i, line in enumerate(lines):
        if not re.match(rf"^\s*{key}\b", line.replace(" ", ""), re.I):
            continue
        for prev in reversed(lines[max(0, i - 3):i]):
            if not prev:
                continue
            return _clean_course_title(prev) if _looks_like_course_title(prev) else ""
    return ""


def parse_text(text: str) -> ParsedSyllabus:
    # Pasted text is very often copied straight out of a PDF, so it arrives
    # with the same broken course banners. The reflow is anchored on the
    # L-T-P marker, so running it on genuinely typed text is a no-op.
    text, _ = _reflow_course_headers(text)
    courses = _backfill_metadata(extract_courses_from_text(text, source="text"), text)
    s = ParsedSyllabus(courses, source="text")
    if not courses:
        s.warnings.append(
            "No course outcomes were recognised. Rows normally look like "
            "'CO1  Explain … K2' — check the text includes the CO table."
        )
    return s


def parse_docx(data: bytes) -> ParsedSyllabus:
    try:
        import docx  # noqa: PLC0415 -- optional dependency, imported on use
    except ImportError:
        raise RuntimeError(
            "Reading .docx needs python-docx. Install it, or paste the CO table as text."
        ) from None

    document = docx.Document(io.BytesIO(data))
    body = "\n".join(p.text for p in document.paragraphs)
    tables_text = "\n".join(
        "\n".join(" | ".join(c.text for c in r.cells) for r in t.rows)
        for t in document.tables
    )
    full = f"{body}\n{tables_text}"

    cos = _cos_from_docx_tables(document)
    source = "docx-table"
    if cos:
        code, title, ltp = _metadata(full)
        courses = [ParsedCourse(code=code, title=title, cos=cos, ltp=ltp)]
    else:
        # No recognisable CO/KL table: fall back to reading the prose.
        source = "docx-text"
        courses = _backfill_metadata(
            extract_courses_from_text(full, source="docx-text"), full)

    s = ParsedSyllabus(courses, source=source)
    if source == "docx-text" and courses:
        s.warnings.append(
            "No CO/KL table was found, so the outcomes were read from the "
            "document text. Check them before continuing."
        )
    if not courses:
        s.warnings.append("No course outcomes were recognised in this document.")
    return s


_SPACED_HYPHEN = re.compile(r"(\w)\s+-(\w)")
_SPLIT_WORD = re.compile(r"\b([A-Za-z]{3,})\s+([a-z]{1,3})\b")


# --- course headers broken across lines by the page layout -----------------
#
# A real AKTU scheme breaks a course banner in three different ways, and each
# one costs the course its identity: the halves match no header shape, so the
# course arrives with no code and no title AND the orphaned lines get
# swallowed into the previous course's last outcome.
#
#   1. the code split at its own hyphen      2. the code split with no hyphen
#        BEC-                                     BAC
#        502 MICROPROCESSOR 3L:1T:0P 4 Credits    052 REMOTE SENSING 3L:0T:0P 3
#
#   3. the title wrapped onto the next line
#        BEC-051
#        IoT - ARCHITECTURE, COMMUNICATION,
#        TECHNOLOGY & ITS APPLICATIONS 3L:0T:0P 3 Credits
#
# Sixteen of the twenty-six courses in the 2025-26 third-year scheme fail this
# way. The join is anchored on the L-T-P marker: a header is only reflowed
# when the continuation actually completes one, so ordinary prose that happens
# to start with capitals is never touched.
_LTP_MARKER = re.compile(r"\d\s*L\s*[:/]\s*\d\s*T\s*[:/]\s*\d\s*P", re.I)
# "BEC-", "BAC", "BEC-051" -- a code, alone on its line.
_CODE_ONLY_LINE = re.compile(r"^[A-Z]{2,4}\s*-?\s*(?:\d{2,4}[A-Z]?)?$")
# The tail of a code that was split off. It may carry the title with it
# ("502 MICROPROCESSOR ...") or arrive alone on its own line, which is
# how a four-way break reads:  "BEC-" / "051" / title / title + L-T-P.
_CODE_TAIL_LINE = re.compile(r"^\d{2,4}[A-Z]?(?:\s|$)")
# A complete code opening a line: "BAC-653A WIRELESS COMMUNICATIONS"
_FULL_CODE_LINE = re.compile(r"^[A-Z]{2,4}\s*-?\s*\d{2,4}[A-Z]?\s+\S")
MAX_HEADER_REFLOW_LINES = 3


# PDF producers emit several characters that look like a hyphen and are
# not one. The AKTU 2025-26 fourth-year scheme writes four of its six
# course codes with U+2010 HYPHEN -- "BEC‐071" -- and two with an
# ASCII hyphen; only the ASCII two were ever recognised, so four courses
# reached the UI with no code and no title. These codepoints ARE hyphens,
# so mapping them is lossless. The en dash is deliberately left alone:
# it is real punctuation in a title ("IoT – Architecture").
_HYPHEN_FAMILY = str.maketrans({
    "\u2010": "-",   # HYPHEN
    "\u2011": "-",   # NON-BREAKING HYPHEN
    "\u2012": "-",   # FIGURE DASH
    "\u2212": "-",   # MINUS SIGN
    "\ufe63": "-",   # SMALL HYPHEN-MINUS
    "\uff0d": "-",   # FULLWIDTH HYPHEN-MINUS
})
# An en or em dash sitting inside a course code is a hyphen whatever the
# typographer intended: letters immediately before, digits immediately
# after, no spaces. Anywhere else it stays as punctuation.
_DASH_IN_CODE = re.compile(r"(?<=[A-Z])[\u2013\u2014](?=\d)")


def _normalise_hyphens(text: str) -> str:
    return _DASH_IN_CODE.sub("-", text.translate(_HYPHEN_FAMILY))


def _reflow_course_headers(text: str) -> tuple[str, int]:
    """Rejoin course banners the layout split. Returns (text, joins)."""
    text = _normalise_hyphens(text)
    lines = text.splitlines()
    out: list[str] = []
    i = joined = 0
    while i < len(lines):
        raw = lines[i]
        head = raw.strip()
        code_only = bool(_CODE_ONLY_LINE.match(head))
        full_code = bool(_FULL_CODE_LINE.match(head))
        if (code_only or full_code) and not _LTP_MARKER.search(head):
            acc, k, done = head, i + 1, False
            while k < len(lines) and (k - i) <= MAX_HEADER_REFLOW_LINES:
                nxt = lines[k].strip()
                if not nxt:                       # blank lines inside a banner
                    k += 1
                    continue
                if len(nxt) > 95 or _NUMBERED_ITEM.match(nxt):
                    break
                # A bare code must be completed by the digits it lost; anything
                # else and this was not a split code at all.
                if acc == head and code_only and not head[-1].isdigit():
                    if not _CODE_TAIL_LINE.match(nxt):
                        break
                    acc = acc.rstrip("- ") + "-" + nxt
                else:
                    acc = acc + " " + nxt
                if _LTP_MARKER.search(nxt):
                    done = True
                    break
                k += 1
            if done:
                out.append(acc)
                joined += 1
                i = k + 1
                continue
        out.append(raw)
        i += 1
    return "\n".join(out), joined


def repair_pdf_text(text: str) -> tuple[str, int]:
    """
    Undo two artifacts that PDF text extraction introduces, and only those.

    **Spaced hyphens.** "state -space", "trade -offs". A space on one side of
    the hyphen only; a real dash in this text ("Low – Power", "5 - 6 weeks")
    has spaces on both, so requiring the asymmetry keeps them intact.

    **Words split mid-token.** "propulsi on" for "propulsion" -- the extractor
    breaks a word where the PDF's kerning jumps. Repairing this needs a
    dictionary, and shipping one for this is disproportionate, so the
    document supplies its own: the fragments are joined only when the joined
    form appears elsewhere in the same file *and* the first fragment does not
    appear as a word on its own. "propulsi"+"on" joins because Unit III says
    "UAV Propulsion" and "propulsi" is never a word; "based on" does not,
    because "based" is.

    Returns the repaired text and the number of split words joined, so the
    caller can say how much it had to correct rather than doing it silently.
    """
    text, _reflowed = _reflow_course_headers(text)
    text = _SPACED_HYPHEN.sub(r"\1-\2", text)

    # The vocabulary is built from the damaged text, so the fragment is in it
    # too -- "propulsi" is a 4+ letter token like any other, and a plain
    # membership test let it veto its own repair. What separates a fragment
    # from a word is that *every* occurrence of it is followed by a short
    # token; a real word like "based" also occurs on its own.
    counts: dict[str, int] = {}
    for w in re.findall(r"[A-Za-z]{3,}", text):
        counts[w.lower()] = counts.get(w.lower(), 0) + 1
    as_head: dict[str, int] = {}
    for m in _SPLIT_WORD.finditer(text):
        key = m.group(1).lower()
        as_head[key] = as_head.get(key, 0) + 1

    joined = 0

    def _join(m: re.Match[str]) -> str:
        nonlocal joined
        head, tail = m.group(1), m.group(2)
        key = head.lower()
        splits = as_head.get(key, 0)
        if counts.get(key, 0) > splits:
            return m.group(0)               # occurs standalone: a real word
        whole = counts.get(key + tail.lower(), 0)
        if whole < 1:
            return m.group(0)               # no evidence for the join
        if whole < splits:
            # The joined form is rarer than the split one, so the split is
            # more likely the real phrase and the "whole" a coincidence.
            # Without this, a document containing both "based on" twice and
            # "Basedon" once would rewrite the phrase.
            return m.group(0)
        joined += 1
        return head + tail

    return _SPLIT_WORD.sub(_join, text), joined


def parse_pdf(data: bytes) -> ParsedSyllabus:
    try:
        from pypdf import PdfReader  # noqa: PLC0415
    except ImportError:
        raise RuntimeError(
            "Reading .pdf needs pypdf. Install it, or paste the CO table as text."
        ) from None

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:                      # a damaged page must not kill the parse
            continue
    text = "\n".join(pages)

    if not text.strip():
        s = ParsedSyllabus(source="pdf-text")
        s.warnings.append(
            "No text could be extracted — this looks like a scanned PDF. "
            "Paste the CO table as text instead."
        )
        return s

    text, repaired = repair_pdf_text(text)

    courses = _backfill_metadata(
        extract_courses_from_text(text, source="pdf-text"), text)
    cos = [c for course in courses for c in course.cos]
    s = ParsedSyllabus(courses, source="pdf-text")
    if repaired:
        s.warnings.append(
            f"PDF extraction split {repaired} word(s) mid-token; they were "
            f"rejoined using words found elsewhere in the same document. "
            f"Worth a glance."
        )
    if not cos:
        s.warnings.append(
            "Text was extracted but no CO rows were recognised. PDF layout "
            "varies — paste the CO table directly if this keeps happening."
        )
    elif any(not c.has_bloom for c in cos):
        s.warnings.append(
            "Some outcomes have no Bloom's level. They are inferred from the "
            "verb where possible, but confirm them."
        )
    return s


def parse_upload(filename: str, data: bytes) -> ParsedSyllabus:
    name = (filename or "").lower()
    if name.endswith(".docx"):
        return parse_docx(data)
    if name.endswith(".pdf"):
        return parse_pdf(data)
    if name.endswith(".doc"):
        raise RuntimeError(
            "Legacy .doc is not supported. Save it as .docx, or paste the CO table."
        )
    return parse_text(data.decode("utf-8", errors="replace"))


def infer_missing_bloom_levels(cos: list[ParsedCO]) -> list[ParsedCO]:
    """
    Fill in a Bloom level from the statement's action verb where the syllabus
    did not tag one. Marked inferred rather than declared, since it is our
    reading of the verb and not the university's.
    """
    from app.engine.blooms import infer_bloom_levels  # noqa: PLC0415 -- cycle

    for co in cos:
        if co.blooms_levels:
            continue
        co.blooms_levels, _ = infer_bloom_levels(co.statement)
        co.bloom_inferred = bool(co.blooms_levels)
    return cos
