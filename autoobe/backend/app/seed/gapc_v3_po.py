"""
GAPC v3 — the legacy 12 Program Outcomes.

Verbatim text cross-checked against a published NBA-accredited institution's
official PO document (gvpcew.ac.in/COs/POs.pdf) during the AutoOBE research
pass, August 2026. This text is the standard NBA/Washington-Accord "Graduate
Attributes" wording used nationally before the GAPC v4.0 transition
(mandatory from Jan 2025). Re-verify against the specific archived NBA
circular before this text is used in a live customer-facing SAR export —
see AutoOBE_Implementation_Plan.pdf, Section 3.2, footnote.

Do not hand-edit this text for style. If NBA's wording changes, add a new
framework version instead of mutating this one — frameworks are immutable
once any customer has live data against them (Section 3.1 of the
implementation plan).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProgramOutcomeSeed:
    po_code: str
    title: str
    statement: str
    # PO "action level" (PL, 1-3) used by the deterministic CO-PO mapping
    # rule (Rule 2 — see app/engine/mapping_rules.py). This is NOT part of
    # NBA's official text: it's an editorial default derived from each PO's
    # dominant verb, meant to be reviewed and overridden by a domain expert
    # per institution. Do not present this number as NBA-prescribed.
    default_action_level: int
    order: int


FRAMEWORK_CODE = "GAPC_V3"
FRAMEWORK_NAME = "Graduate Attributes and Professional Competencies v3.0 (legacy, pre-2025)"

PROGRAM_OUTCOMES: tuple[ProgramOutcomeSeed, ...] = (
    ProgramOutcomeSeed(
        "PO1", "Engineering Knowledge",
        "Apply the knowledge of mathematics, science, engineering fundamentals, "
        "and an engineering specialization to the solution of complex engineering "
        "problems.",
        # PL 2. "APPLY the knowledge of ..." is K3 and the only action here;
        # "the solution of complex engineering problems" is the object, not a
        # second verb. 80.2% against the department's sheet.
        default_action_level=2, order=1,
    ),
    ProgramOutcomeSeed(
        "PO2", "Problem Analysis",
        "Identify, formulate, review research literature, and analyze complex "
        "engineering problems reaching substantiated conclusions using first "
        "principles of mathematics, natural sciences and engineering sciences.",
        # PL 2, and this one needs a judgement rather than a lookup. The four
        # verbs are identify (K1), formulate, review and ANALYZE (K4), all
        # taking the same object -- "complex engineering problems". A Bloom
        # table returns K6 for "formulate", which would make this PL 3.
        #
        # It is rejected: here "formulate" means to state the problem
        # precisely, a stage of analysing it, not to create a solution -- the
        # PO is titled Problem Analysis and analyze is the verb it culminates
        # on. K4 -> PL 2. The department's sheet is unambiguous: PO2 and PO1
        # carry byte-identical columns in all 214 rows, so whatever level PO1
        # has, PO2 has.
        default_action_level=2, order=2,
    ),
    ProgramOutcomeSeed(
        "PO3", "Design/Development of Solutions",
        "Design solutions for complex engineering problems and design system "
        "components or processes that meet the specified needs with appropriate "
        "consideration for the public health and safety, and the cultural, "
        "societal, and environmental considerations.",
        default_action_level=3, order=3,
    ),
    ProgramOutcomeSeed(
        "PO4", "Conduct Investigations of Complex Problems",
        "Use research-based knowledge and research methods including design of "
        "experiments, analysis and interpretation of data, and synthesis of the "
        "information to provide valid conclusions for complex problems.",
        # PL 2, not 3. The action this PO asks for is "USE research-based
        # knowledge and research methods" -- Apply level, K3, action level 2.
        # The word "design" appears only inside the noun phrase "design of
        # experiments", which is what an earlier reading mistook for the PO's
        # own action; PO3 is the design PO and keeps PL 3.
        #
        # Confirmed against the department's own hand-built 2024-25 matrix,
        # where PO4 behaves exactly like PO1/PO2/PO5 (all PL 2) and nothing
        # like PO3: PO4 is recorded 153x3 / 4x2 / 25x1 against PO3's
        # 57x3 / 100x2. See tests/test_bloom_inference.py.
        default_action_level=2, order=4,
    ),
    ProgramOutcomeSeed(
        "PO5", "Modern Tool Usage",
        "Create, select, and apply appropriate techniques, resources, and modern "
        "engineering and IT tools including prediction and modeling to complex "
        "engineering activities with an understanding of the limitations.",
        # PL 3, not 2. The PO opens on "CREATE, select, and apply" -- create is
        # K6, the highest of the three, and it is the culminating action the PO
        # asks for. This is PO3's situation, not PO1's: PO1 asks the graduate to
        # "Apply the knowledge of ..." and stops there.
        #
        # The consequence is the point of the change. An outcome that applies or
        # analyses (CL 2) now maps modern tool usage at 2 -- a moderate
        # contribution -- and only an outcome that actually builds something
        # (CL 3) reaches 3. That is how a human maps a theory course, and it is
        # what a peer team expects: a lecture course that mentions SPICE does
        # not substantially deliver tool usage.
        #
        # It costs agreement with the department's own 2024-25 sheet, which
        # records PO5 flat at 3 for 72% of theory outcomes: that column falls
        # from 78.5% to 48.6% and the whole sheet from 82.6% to 80.5%. The
        # department asked for the human reading over the replica; the number
        # is recorded here so the trade is visible rather than discovered.
        default_action_level=3, order=5,
    ),
    ProgramOutcomeSeed(
        "PO6", "The Engineer and Society",
        "Apply reasoning informed by the contextual knowledge to assess societal, "
        "health, safety, legal and cultural issues and the consequent "
        "responsibilities relevant to the professional engineering practice.",
        # PL 2 and checked, not merely inherited. The action asked of the
        # graduate is "APPLY reasoning" -- K3. "to assess" is an infinitive of
        # purpose describing what the reasoning is for, the same construction
        # as PO4's "design of experiments", and reading it as the action would
        # give K5 and PL 3. The department's own sheet settles it: at PL 2
        # this column agrees with them on 83.3% of the cells they filled, at
        # PL 3 on 53.3%.
        #
        # v4 restates this PO as "ANALYZE AND EVALUATE societal and
        # environmental aspects", which genuinely is PL 3. The two frameworks
        # differ here because the statements differ, not by oversight.
        default_action_level=2, order=6,
    ),
    ProgramOutcomeSeed(
        "PO7", "Environment and Sustainability",
        "Understand the impact of the professional engineering solutions in "
        "societal and environmental contexts, and demonstrate the knowledge of, "
        "and need for sustainable development.",
        # PL 2. "Understand" alone is K2, but the second predicate
        # "DEMONSTRATE the knowledge of ..." is K3, and it is the higher of
        # the two actions this PO asks for. Their sheet agrees: 65.4% at PL 2
        # against 30.8% at PL 1.
        default_action_level=2, order=7,
    ),
    ProgramOutcomeSeed(
        "PO8", "Ethics",
        "Apply ethical principles and commit to professional ethics and "
        "responsibilities and norms of the engineering practice.",
        # PL 2, not 1. "APPLY ethical principles" is K3, exactly as PO1's
        # "Apply the knowledge of ..." is K3. PO8, PO9 and PO10 all carried
        # PL 1 -- a blanket assumption that the non-technical POs sit at the
        # bottom of the scale, which is not what any of the three statements
        # says. Measured against the department's sheet, this column goes from
        # 20.0% to 73.3%: the single largest correction in the framework.
        default_action_level=2, order=8,
    ),
    ProgramOutcomeSeed(
        "PO9", "Individual and Team Work",
        "Function effectively as an individual, and as a member or leader in "
        "diverse teams, and in multidisciplinary settings.",
        # PL 2, not 1. "Function" is not a cognitive verb at all, so the level
        # cannot be read off a Bloom table -- but functioning effectively as a
        # member or leader of a team is performing a skill, which is the Apply
        # band. Their sheet is decisive where the text is not: 39% at PL 1
        # against 69% at PL 2, over the 29 cells they filled here.
        default_action_level=2, order=9,
    ),
    ProgramOutcomeSeed(
        "PO10", "Communication",
        "Communicate effectively on complex engineering activities with the "
        "engineering community and with society at large, such as, being able "
        "to comprehend and write effective reports and design documentation, "
        "make effective presentations, and give and receive clear instructions.",
        # PL 2, not 1. COMMUNICATE, write, make -- all K3. The only K6-looking
        # word in the statement is inside the compound noun "design
        # documentation", which is documentation OF designs and not an
        # instruction to design anything; reading it as an action would put
        # this PO at PL 3. That misreading is now fixed for every statement in
        # blooms._COMPOUND_NOUN_HEAD rather than excused here.
        # 50.0% -> 55.0% against their sheet, and 9.5% -> 66.7% on the
        # non-technical courses where this column is actually populated.
        default_action_level=2, order=10,
    ),
    ProgramOutcomeSeed(
        "PO11", "Project Management and Finance",
        "Demonstrate knowledge and understanding of the engineering and "
        "management principles and apply these to one's own work, as a member "
        "and leader in a team, to manage projects and in multidisciplinary "
        "environments.",
        # PL 2. "Demonstrate knowledge and understanding ... and APPLY these
        # to one's own work" -- apply is K3 and the higher of the two.
        # Checked at PL 3 as well, since "manage projects" reads like a
        # higher-order act: 57.5% at PL 2 against 52.5% at PL 3, so the text
        # and the sheet agree and the value stands.
        default_action_level=2, order=11,
    ),
    ProgramOutcomeSeed(
        "PO12", "Life-long Learning",
        "Recognize the need for, and have the preparation and ability to "
        "engage in independent and lifelong learning in the broadest context "
        "of technological change.",
        # PL 1, and after the audit the ONLY PL 1 in this framework.
        # "RECOGNIZE the need for" is K1; nothing else in the statement asks
        # the graduate to do anything. That is why the life-long learning
        # convention produces a full column of 3s -- CL >= 1 always holds --
        # and why it does so honestly rather than by assertion. 96.7% against
        # their sheet, and 100% across the technical courses.
        default_action_level=1, order=12,
    ),
)

assert len(PROGRAM_OUTCOMES) == 12, "GAPC v3 must carry exactly 12 program outcomes"
