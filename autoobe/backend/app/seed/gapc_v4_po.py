"""
GAPC v4.0 — the current 11 Program Outcomes, plus the WK1-WK9 Knowledge and
Attitude Profile.

Transcribed byte-exact from Annexure I and Annexure II of the official NBA
SAR: "Self-Assessment Report Format, Undergraduate Engineering Programs,
Graduate Attributes and Professional Competencies Version 4.0 (GAPC V4.0),
TIER-II Institution Programs", January 2025, nbaind.org. Fetched and read
in full during the AutoOBE research pass, August 2026.

Do not hand-edit this text for style, and do not merge it with the GAPC v3
module above — see AutoOBE_Implementation_Plan.pdf, Section 3.1: both
frameworks are permanent, first-class, and coexist for as long as any
customer has live data against either one.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProgramOutcomeSeed:
    po_code: str
    title: str
    statement: str
    wk_refs: tuple[str, ...]
    # See the identical field in gapc_v3_po.py — same caveat applies: this
    # is an editorial default for the deterministic mapping rule, not part
    # of NBA's official text.
    default_action_level: int
    order: int


@dataclass(frozen=True)
class KnowledgeProfileSeed:
    wk_code: str
    statement: str
    order: int


FRAMEWORK_CODE = "GAPC_V4"
FRAMEWORK_NAME = "Graduate Attributes and Professional Competencies v4.0 (Jan 2025)"

PROGRAM_OUTCOMES: tuple[ProgramOutcomeSeed, ...] = (
    ProgramOutcomeSeed(
        "PO1", "Engineering Knowledge",
        "Apply knowledge of mathematics, natural science, computing, engineering "
        "fundamentals and an engineering specialization as specified in WK1 to "
        "WK4 respectively to develop to the solution of complex engineering "
        "problems.",
        wk_refs=("WK1", "WK2", "WK3", "WK4"), default_action_level=2, order=1,
    ),
    ProgramOutcomeSeed(
        "PO2", "Problem Analysis",
        "Identify, formulate, review research literature and analyze complex "
        "engineering problems reaching substantiated conclusions with "
        "consideration for sustainable development.",
        wk_refs=("WK1", "WK2", "WK3", "WK4"), default_action_level=2, order=2,
    ),
    ProgramOutcomeSeed(
        "PO3", "Design/Development of Solutions",
        "Design creative solutions for complex engineering problems and "
        "design/develop systems/components/processes to meet identified needs "
        "with consideration for the public health and safety, whole-life cost, "
        "net zero carbon, culture, society and environment as required.",
        wk_refs=("WK5",), default_action_level=3, order=3,
    ),
    ProgramOutcomeSeed(
        "PO4", "Conduct Investigations of Complex Problems",
        "Conduct investigations of complex engineering problems using "
        "research-based knowledge including design of experiments, modelling, "
        "analysis & interpretation of data to provide valid conclusions.",
        # PL 2 for the same reason as GAPC v3: the PO's own action is
        # "CONDUCT investigations" -- Apply level, K3, action level 2. The
        # only "design" here is the noun phrase "design of experiments".
        # PO3 remains the design PO at PL 3.
        wk_refs=("WK8",), default_action_level=2, order=4,
    ),
    ProgramOutcomeSeed(
        "PO5", "Engineering Tool Usage",
        "Create, select and apply appropriate techniques, resources and modern "
        "engineering & IT tools, including prediction and modelling recognizing "
        "their limitations to solve complex engineering problems.",
        # PL 3 -- "CREATE, select and apply", K6, exactly as in v3. See the
        # note on PO5 in gapc_v3_po.py for the reasoning and its measured cost.
        wk_refs=("WK2", "WK6"), default_action_level=3, order=5,
    ),
    ProgramOutcomeSeed(
        "PO6", "The Engineer and The World",
        "Analyze and evaluate societal and environmental aspects while solving "
        "complex engineering problems for its impact on sustainability with "
        "reference to economy, health, safety, legal framework, culture and "
        "environment.",
        # PL 3, not 2. "ANALYZE AND EVALUATE" -- K4 and K5, both finite verbs
        # taking the same object. v3 states this attribute as "Apply reasoning
        # ... to assess", where the action is apply (K3, PL 2) and "to assess"
        # is the purpose; v4 promotes the assessment to the main verb. The two
        # frameworks differ here because the statements differ, and the
        # crosswalk in frameworks.py already treats them as one attribute with
        # two wordings.
        wk_refs=("WK1", "WK5", "WK7"), default_action_level=3, order=6,
    ),
    ProgramOutcomeSeed(
        "PO7", "Ethics",
        "Apply ethical principles and commit to professional ethics, human "
        "values, diversity and inclusion; adhere to national & international "
        "laws.",
        # PL 2 -- "APPLY ethical principles", K3. See PO8 in gapc_v3_po.py:
        # the ethics, teamwork and communication POs all carried PL 1 on a
        # blanket assumption that the non-technical outcomes sit at the bottom
        # of the scale, which none of the three statements says.
        wk_refs=("WK9",), default_action_level=2, order=7,
    ),
    ProgramOutcomeSeed(
        "PO8", "Individual and Collaborative Team work",
        "Function effectively as an individual, and as a member or leader in "
        "diverse/multi-disciplinary teams.",
        # PL 2. "Function" is not a cognitive verb, so this is judgement plus
        # evidence rather than a lookup: performing effectively as a member or
        # leader is the Apply band, and the department's v3 sheet agrees on
        # the equivalent column, 39% at PL 1 against 69% at PL 2.
        wk_refs=(), default_action_level=2, order=8,
    ),
    ProgramOutcomeSeed(
        "PO9", "Communication",
        "Communicate effectively and inclusively within the engineering "
        "community and society at large, such as being able to comprehend and "
        "write effective reports and design documentation, make effective "
        "presentations considering cultural, language, and learning "
        "differences.",
        # PL 2. Communicate, write, make -- all K3. The only K6-looking word
        # is inside the compound noun "design documentation"; see PO10 in
        # gapc_v3_po.py and blooms._COMPOUND_NOUN_HEAD.
        wk_refs=(), default_action_level=2, order=9,
    ),
    ProgramOutcomeSeed(
        "PO10", "Project Management and Finance",
        "Apply knowledge and understanding of engineering management "
        "principles and economic decision-making and apply these to one's own "
        "work, as a member and leader in a team, and to manage projects and in "
        "multidisciplinary environments.",
        wk_refs=(), default_action_level=2, order=10,
    ),
    ProgramOutcomeSeed(
        "PO11", "Life-Long Learning",
        "Recognize the need for, and have the preparation and ability for "
        "i) independent and life-long learning ii) adaptability to new and "
        "emerging technologies and iii) critical thinking in the broadest "
        "context of technological change.",
        wk_refs=("WK8",), default_action_level=1, order=11,
    ),
)

KNOWLEDGE_PROFILE: tuple[KnowledgeProfileSeed, ...] = (
    KnowledgeProfileSeed(
        "WK1",
        "A systematic, theory-based understanding of the natural sciences "
        "applicable to the discipline and awareness of relevant social "
        "sciences.",
        order=1,
    ),
    KnowledgeProfileSeed(
        "WK2",
        "Conceptually-based mathematics, numerical analysis, data analysis, "
        "statistics and formal aspects of computer and information science to "
        "support detailed analysis and modelling applicable to the discipline.",
        order=2,
    ),
    KnowledgeProfileSeed(
        "WK3",
        "A systematic, theory-based formulation of engineering fundamentals "
        "required in the engineering discipline.",
        order=3,
    ),
    KnowledgeProfileSeed(
        "WK4",
        "Engineering specialist knowledge that provides theoretical "
        "frameworks and bodies of knowledge for the accepted practice areas "
        "in the engineering discipline; much is at the forefront of the "
        "discipline.",
        order=4,
    ),
    KnowledgeProfileSeed(
        "WK5",
        "Knowledge, including efficient resource use, environmental impacts, "
        "whole-life cost, re-use of resources, net zero carbon, and similar "
        "concepts, that supports engineering design and operations in a "
        "practice area.",
        order=5,
    ),
    KnowledgeProfileSeed(
        "WK6",
        "Knowledge of engineering practice (technology) in the practice "
        "areas in the engineering discipline.",
        order=6,
    ),
    KnowledgeProfileSeed(
        "WK7",
        "Knowledge of the role of engineering in society and identified "
        "issues in engineering practice in the discipline, such as the "
        "professional responsibility of an engineer to public safety and "
        "sustainable development.",
        order=7,
    ),
    KnowledgeProfileSeed(
        "WK8",
        "Engagement with selected knowledge in the current research "
        "literature of the discipline, awareness of the power of critical "
        "thinking and creative approaches to evaluate emerging issues.",
        order=8,
    ),
    KnowledgeProfileSeed(
        "WK9",
        "Ethics, inclusive behavior and conduct. Knowledge of professional "
        "ethics, responsibilities, and norms of engineering practice. "
        "Awareness of the need for diversity by reason of ethnicity, gender, "
        "age, physical ability etc. with mutual understanding and respect, "
        "and of inclusive attitudes.",
        order=9,
    ),
)

assert len(PROGRAM_OUTCOMES) == 11, "GAPC v4.0 must carry exactly 11 program outcomes"
assert len(KNOWLEDGE_PROFILE) == 9, "GAPC v4.0 must carry exactly 9 knowledge/attitude profiles"

# The crosswalk from AutoOBE_Implementation_Plan.pdf, Section 3.4 — kept as
# data (not just documentation) so the migration tool and any cross-framework
# trend view can use it directly instead of re-deriving it.
CROSSWALK_V3_TO_V4: dict[str, tuple[str, ...]] = {
    "PO1": ("PO1",), "PO2": ("PO2",), "PO3": ("PO3",), "PO4": ("PO4",), "PO5": ("PO5",),
    "PO6": ("PO6",),  # merges with old PO7 below
    "PO7": ("PO6",),  # Environment & Sustainability folds into "The Engineer and The World"
    "PO8": ("PO7",), "PO9": ("PO8",), "PO10": ("PO9",), "PO11": ("PO10",), "PO12": ("PO11",),
}
