"""
Curriculum library -- a small AKTU-shaped starter corpus.

The plan calls the parsed curriculum corpus the real moat: a competitor can
copy a mapping engine in a quarter, but several hundred hand-verified courses
per branch with correct K-level arrays is months of unglamorous work. This
module is the seed of that corpus and the shape the batch parser must emit.

CO statements here are written to be NBA-compliant (measurable, action-verb
led, single dominant Bloom level). Real AKTU syllabus COs frequently are not
-- that gap is the CO-rewriter's job, and `AKTU_RAW_SAMPLES` below keeps a few
verbatim non-compliant examples so the rewriter has honest test material.

K-levels are arrays because current AKTU syllabi genuinely tag COs "K3, K4".
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SeedCO:
    code: str
    statement: str
    blooms_levels: list[int]


@dataclass(frozen=True)
class SeedCourse:
    code: str
    title: str
    branch: str
    semester: int
    ltp: tuple[int, int, int]          # AKTU publishes L-T-P, not NCrF hours
    credits: int
    cos: list[SeedCO]
    home_pos: tuple[str, ...] = ()     # POs this course exists to deliver
    scheme: str = "AKTU-2024-25"
    units: list[str] = field(default_factory=list)
    # A representative question paper, so the question->CO tagger can be
    # exercised without the user having to supply one first. Written to match
    # the style of real AKTU papers: a command verb, then the subject.
    sample_paper: str = ""

    @property
    def ncrf_hours(self) -> dict[str, int]:
        """
        Convert L-T-P into the CI / LI / (TW+SL) hours SAR Table 1.2.1.1
        requires under the National Credit Framework (30 hours = 1 credit).

        AKTU has not done this conversion upstream -- verified by grepping
        current syllabus PDFs, which contain no NCrF reference at all -- so
        every affiliated college has to do it downstream to fill the table.
        A 15-week semester is assumed; that assumption must be stated in the
        methodology note, since it is an institutional convention, not a
        national standard.
        """
        weeks = 15
        lecture, tutorial, practical = self.ltp
        ci = (lecture + tutorial) * weeks
        li = practical * weeks
        total = self.credits * 30
        return {"CI": ci, "LI": li, "TW_SL": max(0, total - ci - li), "total": total}


CATALOGUE: tuple[SeedCourse, ...] = (
    SeedCourse(
        code="KCS501", title="Database Management Systems", branch="CSE", semester=5,
        ltp=(3, 1, 0), credits=4, home_pos=("PO1", "PO2"),
        units=["Introduction & ER model", "Relational model & SQL",
               "Normalization", "Transaction processing", "Concurrency & recovery"],
        sample_paper="""1. Explain the three-schema architecture of a DBMS with a neat diagram.
2. Draw an ER diagram for a library management system and convert it to a relational schema.
3. Write SQL queries to find the second highest salary from an employee table.
4. Determine whether the given schedule S is conflict serializable.
5. Compare two-phase locking with timestamp ordering for concurrency control.""",
        cos=[
            SeedCO("CO1", "Explain the architecture of a database management system and the "
                          "relational data model.", [2]),
            SeedCO("CO2", "Apply entity-relationship modelling to design a normalized "
                          "relational schema for a given application.", [3]),
            SeedCO("CO3", "Construct SQL queries to retrieve and manipulate data from a "
                          "relational database.", [3, 6]),
            SeedCO("CO4", "Analyze transaction schedules for serializability and recoverability.", [4]),
            SeedCO("CO5", "Evaluate concurrency control and recovery techniques for a given "
                          "workload.", [5]),
        ],
    ),
    SeedCourse(
        code="KCS502", title="Operating Systems", branch="CSE", semester=5,
        ltp=(3, 0, 0), credits=3, home_pos=("PO1", "PO2"),
        units=["OS structure & processes", "CPU scheduling", "Deadlocks",
               "Memory management", "File systems & I/O"],
        sample_paper="""1. Explain the functions and structure of an operating system.
2. Compute the average waiting time using SJF scheduling for the given process set.
3. Apply the banker's algorithm to determine whether the system is in a safe state.
4. Compare paging with segmentation in terms of fragmentation.
5. Analyze SCAN and C-SCAN disk scheduling for the given request queue.""",
        cos=[
            SeedCO("CO1", "Explain the functions and structure of an operating system.", [2]),
            SeedCO("CO2", "Apply CPU scheduling algorithms to compute waiting and turnaround "
                          "times for a given process set.", [3]),
            SeedCO("CO3", "Analyze deadlock conditions and apply avoidance and detection "
                          "algorithms.", [3, 4]),
            SeedCO("CO4", "Compare memory management and paging techniques in terms of "
                          "fragmentation and performance.", [4]),
            SeedCO("CO5", "Analyze file system and disk scheduling strategies for a stated "
                          "workload.", [4]),
        ],
    ),
    SeedCourse(
        code="KCS503", title="Design and Analysis of Algorithms", branch="CSE", semester=5,
        ltp=(3, 1, 0), credits=4, home_pos=("PO1", "PO2", "PO3"),
        units=["Asymptotic analysis", "Divide and conquer", "Greedy methods",
               "Dynamic programming", "NP-completeness"],
        sample_paper="""1. Analyze the time complexity of the given recursive algorithm using the master theorem.
2. Apply the divide-and-conquer strategy to sort the given array using merge sort.
3. Design a dynamic programming solution for the 0/1 knapsack problem.
4. Compare greedy and dynamic programming approaches for the given optimization problem.
5. Explain the classes P, NP and NP-complete with examples.""",
        cos=[
            SeedCO("CO1", "Analyze the asymptotic time and space complexity of algorithms.", [4]),
            SeedCO("CO2", "Apply divide-and-conquer and greedy strategies to solve "
                          "computational problems.", [3]),
            SeedCO("CO3", "Design dynamic programming solutions for optimization problems.", [6]),
            SeedCO("CO4", "Compare algorithm design paradigms for a given problem in terms of "
                          "efficiency.", [4]),
            SeedCO("CO5", "Explain the classes P, NP and NP-complete and identify problems "
                          "within them.", [2, 3]),
        ],
    ),
    SeedCourse(
        code="KCS551", title="Database Management Systems Lab", branch="CSE", semester=5,
        ltp=(0, 0, 2), credits=1, home_pos=("PO5",),
        units=["DDL & DML", "Joins & sub-queries", "PL/SQL blocks",
               "Triggers & cursors", "Mini project"],
        cos=[
            SeedCO("CO1", "Implement relational schemas using SQL data definition statements.", [3]),
            SeedCO("CO2", "Construct SQL queries using joins, sub-queries and aggregate "
                          "functions.", [3, 6]),
            SeedCO("CO3", "Develop PL/SQL procedures, triggers and cursors for a stated "
                          "requirement.", [6]),
            SeedCO("CO4", "Demonstrate transaction control and user privilege management "
                          "using a modern database tool.", [3]),
        ],
    ),
    SeedCourse(
        code="KEC601", title="Digital Communication", branch="ECE", semester=6,
        ltp=(3, 1, 0), credits=4, home_pos=("PO1", "PO2"),
        units=["Sampling & quantization", "Baseband transmission",
               "Digital modulation", "Information theory", "Error control coding"],
        sample_paper="""1. Explain the block diagram of a digital communication system.
2. Apply the sampling theorem to determine the Nyquist rate for the given signal.
3. Analyze the bit error rate performance of BPSK over an AWGN channel.
4. Design a digital communication link meeting a specified BER and bandwidth budget.
5. Evaluate the coding gain of a convolutional code for the given channel.""",
        cos=[
            SeedCO("CO1", "Explain the fundamental principles of digital communication "
                          "systems.", [2]),
            SeedCO("CO2", "Apply sampling and quantization techniques to convert analog "
                          "signals to digital form.", [3]),
            SeedCO("CO3", "Analyze the performance of digital modulation techniques in terms "
                          "of bandwidth and error probability.", [4]),
            SeedCO("CO4", "Design a digital communication system to satisfy specified "
                          "performance requirements.", [6]),
            SeedCO("CO5", "Evaluate error control coding schemes for a given channel "
                          "condition.", [5]),
        ],
    ),
    SeedCourse(
        code="KME402", title="Engineering Thermodynamics", branch="ME", semester=4,
        ltp=(3, 1, 0), credits=4, home_pos=("PO1",),
        units=["Basic concepts", "First law", "Second law & entropy",
               "Properties of pure substances", "Power cycles"],
        sample_paper="""1. Explain the zeroth and first laws of thermodynamics with examples.
2. Apply the first law to a closed system undergoing a polytropic process.
3. Analyze an irreversible process using entropy change of the universe.
4. Evaluate the thermal efficiency of the given Rankine cycle.""",
        cos=[
            SeedCO("CO1", "Explain the fundamental concepts and laws of thermodynamics.", [2]),
            SeedCO("CO2", "Apply the first law of thermodynamics to closed and open systems.", [3]),
            SeedCO("CO3", "Analyze thermodynamic processes using the second law and entropy "
                          "principles.", [4]),
            SeedCO("CO4", "Evaluate the performance of power and refrigeration cycles.", [5]),
        ],
    ),
    SeedCourse(
        code="KOE068", title="Environmental Engineering", branch="CE", semester=6,
        ltp=(3, 0, 0), credits=3, home_pos=("PO6", "PO7"),
        units=["Ecosystems & biodiversity", "Water & air pollution",
               "Solid waste management", "Environmental legislation",
               "Sustainable development"],
        cos=[
            SeedCO("CO1", "Explain the structure of ecosystems and the impact of engineering "
                          "activity on biodiversity.", [2]),
            SeedCO("CO2", "Analyze sources and effects of water and air pollution on public "
                          "health and the environment.", [4]),
            SeedCO("CO3", "Evaluate solid waste management practices against sustainability "
                          "and whole-life cost criteria.", [5]),
            SeedCO("CO4", "Apply environmental legislation and ethical principles to "
                          "engineering practice.", [3]),
        ],
    ),
    SeedCourse(
        code="KCS753", title="Major Project", branch="CSE", semester=7,
        ltp=(0, 0, 8), credits=4, home_pos=("PO3", "PO8", "PO9", "PO10", "PO11"),
        units=["Problem identification", "Literature survey", "Design & implementation",
               "Testing & validation", "Report & presentation"],
        cos=[
            SeedCO("CO1", "Identify a complex engineering problem and survey the current "
                          "research literature relevant to it.", [4]),
            SeedCO("CO2", "Design and develop a solution using appropriate modern engineering "
                          "tools.", [6]),
            SeedCO("CO3", "Evaluate the solution against societal, environmental, safety and "
                          "whole-life cost considerations.", [5]),
            # NBA's PO8 says "Function effectively", but "function" is not
            # directly observable, so the outcome names what is actually
            # assessed by the project rubric instead.
            SeedCO("CO4", "Demonstrate effective teamwork and manage the project schedule "
                          "and cost within stated constraints.", [3]),
            SeedCO("CO5", "Communicate the work through an effective technical report and "
                          "presentation.", [3]),
        ],
    ),
)

BY_CODE: dict[str, SeedCourse] = {c.code: c for c in CATALOGUE}

# Verbatim non-compliant CO statements from current AKTU syllabi, kept as
# honest test material for the CO quality checker and rewriter. Neither is
# measurable, student-centric, or led by a Bloom action verb.
AKTU_RAW_SAMPLES: tuple[str, ...] = (
    "Student should be aware of techniques used for classification and clustering",
    "To provide hardware and software issues in modern distributed systems",
)


def list_courses(branch: str | None = None) -> list[SeedCourse]:
    courses = list(CATALOGUE)
    if branch:
        courses = [c for c in courses if c.branch.upper() == branch.upper()]
    return sorted(courses, key=lambda c: (c.semester, c.code))


def branches() -> list[str]:
    return sorted({c.branch for c in CATALOGUE})
