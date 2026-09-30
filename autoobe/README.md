# AutoOBE

CO–PO mapping and NBA attainment for engineering programs. Automatic by
default, overridable at every step, and auditable afterwards.

## Run it

```bash
cd backend
pip install -r requirements.txt
python -m pytest                                  # 805 tests
python demo.py                                    # whole pipeline, terminal
python -m app.engine.copo_eval                    # measured CO → PO accuracy
python -m app.engine.tagger_eval                  # measured tagger accuracy
python -m uvicorn app.api.main:app --port 8010    # UI at http://127.0.0.1:8010
```

Nothing external is required to run: persistence is SQLite, and the engine is
pure functions over plain data, which is what makes the regression suite
meaningful. `AUTOOBE_DB=:memory:` makes a session ephemeral.

## One admin, many teachers, one sheet

The engine above is single-user and stores nothing. A department needs more
than that: every teacher maps their own subject, and one person holds the
sheet that all of it adds up to. That is what `/login`, `/teacher` and
`/admin` are, on a Neon Postgres database.

```bash
npm i -g neon@latest && neon login
neon link --project-id <your-project> --branch production -y   # writes .env.local
python -m uvicorn app.api.main:app --port 8010                 # then open /login
```

The first visit to `/login` offers to create the administrator, because the
`users` table has none. **That window closes the moment it is used** and never
reopens — after that the admin creates every account.

| Who | Can |
| --- | --- |
| **Administrator** | see, edit, delete and download every subject and every mapping; add teachers; assign subjects; read the programme's PO coverage |
| **Teacher** | map the subjects assigned to them, submit, and change their own password |

### Getting forty-four subjects in without typing forty-four subjects

A department's sheet is not one course, and typing it in one row at a time is
the difference between this being used and not. The dashboard offers three
ways, all landing on the same endpoint:

* **From the AKTU catalogue** — search the 749 bundled courses by code or
  name; the code, title and course kind come out right because they are the
  university's own.
* **Paste a list** — `BEC-302, Digital System Design` per line, comma, tab or
  spaced dash. A duplicate in the middle does not throw away the rows either
  side of it: each is attempted alone and the failures come back with reasons.
* **One at a time**, for the odd course neither of those covers.

### The programme, not just the course

A course block answers *"does this subject serve PO4?"*. Only the union of
every submitted block answers *"does this **programme** serve PO4?"* — and
that is the question a peer team opens the file with. The dashboard shows
every PO averaged over the outcomes that reach it and then over the subjects
that do, with **the columns nothing reaches drawn in red**. A subject that
leaves PO6 blank does not drag the programme's PO6 average down; it simply is
not part of it, and is counted as the gap it is.

### Mapping the wrong subject

A teacher opens their assignment, drops a whole-semester PDF in, and picks a
course card. Nothing stops them picking the wrong one — and the mapping would
then be filed against the right subject with another course's outcomes, which
nobody downstream can see, because the sheet only ever shows the subject name.

So the two are compared and the difference is said out loud, in the bar and
again in a confirmation before Submit. It warns rather than blocks: a
department that codes its subjects differently from AKTU is not wrong, and
only the teacher knows.

### Nothing is stored until Submit

The department's rule, verbatim: *"if the teacher don't hit submit button then
the data is not stored."* It is honoured literally — **there is no drafts
table**, and that absence is the design. A mapping exists in the database
because somebody pressed Submit, so work in progress is invisible to the admin
by construction rather than by a status flag someone has to remember to check.

Taken alone that rule loses a teacher forty minutes of work to a stray
Ctrl-W, so the two halves are resolved by *where* the draft lives: the
in-progress matrix is written to that teacher's own `localStorage` on every
edit, and cleared the moment it is submitted. No request carries it, the
department cannot see it, and the rule holds as stated. The teacher's
dashboard says which of the two states each subject is in — *not started*,
*draft on this computer*, *submitted*.

### Internal CO attainment

The other half of what a teacher owes the department, and the one their
`CO Attainment 2023-24 even.xlsx` already holds: a marks table, one row per
student and one column per CO, reduced to a level per CO and a course average.

That workbook is the specification for `engine/co_attainment.py`, and the
module was written by reproducing it rather than from a definition — **all
eleven subjects across three semesters come back cell for cell**, which is the
only reason to trust it. Two targets, and they are different kinds of thing:

| | is | default |
| --- | --- | --- |
| **T1** | a **mark** — the share of that CO's maximum a student must reach | 45% |
| **T2** | a **share of students** — how many must cross T1 to score the full 3 | 60% |

    attainment = min(3, crossing% / T2 * 3)

Proportional, not banded — deliberately *not* the rule in `attainment.py`,
which bands a success percentage against three thresholds and works per exam
question. Both are legitimate; this one is theirs.

Three details in their sheet each move a published figure if read wrongly, and
each is pinned by a test:

* **An absent student is not a zero.** `AB` and `DB` leave that CO's
  denominator. Their sheet has 135 students appearing for CO1 of a subject and
  176 for CO3 of the same one.
* **The summary rows are not students.** The five rows under the class are
  full of numbers in the CO columns; reading them as people put 182 students
  in a class of 176 and moved every figure in the file.
* **One mark is stored as 10.799999999999999** against a T1 of 10.8. Excel
  counts that student as crossing and an exact float comparison does not, so
  both sides are rounded to two places first.

A teacher uploads that workbook, picks their subject block out of the five
side by side (the one matching their code is selected for them), adjusts T1
and T2 if their course runs a different scheme, and watches the five summary
rows recompute. **Nothing is stored until Submit**, exactly as with the
mapping. The admin then sees every course's per-CO levels, its internal
attainment and the programme mean.

### PO attainment — the mapping times the marks

The two halves above are each only half. A CO→PO mapping says which outcomes
serve which programme outcome; a CO attainment says how well each outcome was
met. Neither is PO attainment. The product is, and it is the number the whole
exercise exists to produce — **SAR Table 3.8.1**, and the input to Criterion 8:

    PO(j) = Σ attainment(COᵢ) × weight(COᵢ, POⱼ)  ÷  Σ weight(COᵢ, POⱼ)

per course, then the mean over the courses that address that PO.

Three decisions in that sentence are worth stating, because each is a way to
get it quietly wrong:

* **Only courses with both halves count.** A course with a mapping and no
  marks would drag the average toward whichever half it has, so it is excluded
  — and reported, so the gap is a visible fact rather than an absorbed one:
  *"Computed from 2 of 3 subjects. Still waiting: 1 mapped but not yet
  measured."*
* **A PO no course addresses is null, not zero.** *Not taught here* and
  *taught and not attained* are different findings and a peer team reads them
  differently. Zero says the second.
* **An untouched PO is never a CQI action.** It is a curriculum gap, and it is
  listed as one.

### Closing the loop

Criterion 8 asks what was *done* about the gaps. Every PO under the
department's target — theirs to set, 2.0 of 3 by default — gets a drafted
action and a severity that moves with the gap. Drafted, not written: it is a
starting point for the sentence a department writes, never a substitute for
it.

`Download the SAR tables (.xlsx)` gives three sheets from the same submitted
data the dashboard shows, so what a peer team reads and what the department
sees cannot drift apart:

| | |
| --- | --- |
| **Table 3.8.1** | courses × POs, with the Direct Attainment row |
| **CQI actions** | every PO under target, its gap, severity and drafted action |
| **Basis** | how many courses have each half — a table is only as honest as its denominator |

### The older routes are guarded too

`workspace.py` — audit, approvals, PSOs, surveys, trends, the exports — used
to take the actor and role from `X-Actor` / `X-Role` headers and believe them.
That was defensible while this was a single-user offline workbench behind an
institutional login, and stopped being so the moment it grew accounts and a
shared database: **anyone who could reach the API could read the audit trail
by asserting a header.**

The rule is now conditional on the install rather than on the route:

| install | behaviour |
| --- | --- |
| no `DATABASE_URL` | open, exactly as before — a single-user copy with nobody to sign in as |
| database, no admin yet | open, so it can be set up before it is locked |
| accounts exist | a valid session, and no header substitutes for one |

The headers still name the actor *within* an authenticated request, so the
approval chain records who did what. What they no longer do is decide whether
the request is allowed at all.

This also hardened the test suite: `conftest.py` now sets `DATABASE_URL=""`,
and `neon.database_url()` treats an explicitly-set value as authoritative even
when empty. Without that, `.env.local` is found by the walk-up and the offline
suite runs against **production** — which is how a fixture's `DELETE` could
have taken the real sheet.

### Isolation is in the query, not the route

A teacher's subject list is `WHERE teacher_id = %s` with their own id bound by
the store, and submitting is checked with `teacher_owns` before anything is
written. A route that forgot its guard would return an empty list rather than
somebody else's subject. This matters because the older `workspace.py` router
takes its actor from a request header and trusts it — that is fine behind an
institutional login and is *not* what this layer does.

Passwords are `hashlib.scrypt` with a per-password salt, and the parameters
travel in the stored hash so they can be raised later without invalidating
accounts. Changing a password deletes every session on that account. Sessions
are opaque tokens in an HttpOnly cookie, twelve hours.

### It stays optional

`DATABASE_URL` absent means this whole layer is dormant: every route in it
answers 503 with a sentence saying so, and the mapping engine runs exactly as
it does offline. That is what keeps the regression suite honest — 758 of the
768 tests never open a socket.

`tests/test_accounts.py` is deliberate about this too. `neon.database_url()`
finds `.env.local` by walking up the tree, so a test that simply called the
store would connect to **production** and its first `DELETE` would take the
real sheet with it. The destructive tests therefore run only when
`AUTOOBE_TEST_DATABASE_URL` names a different database, and refuse outright if
it matches the live one:

```bash
neon branches create --name autoobe-test
AUTOOBE_TEST_DATABASE_URL=$(neon connection-string autoobe-test)   python -m pytest tests/test_accounts.py        # 20 pass
```

## The site is CO → PO mapping

`/` is one screen doing one job: **drop an AKTU syllabus PDF → outcomes and
Bloom's levels → the CO × PO matrix → the PO averages, downloadable**, with the
rule behind every cell one click away. Five steps, no other tabs, nothing to
author first.

**Light, dark, or follow the machine.** Three states rather than two, because
"follow the system" is a real preference and not the same as either fixed
choice. The choice is remembered and applied before the first paint, so there
is no flash of the wrong theme on load; a browser that refuses storage falls
back to following the system rather than failing.

**The layout uses the whole window.** There is no centred column: on a wide
monitor a capped width leaves a third of the screen empty, and this is a
working tool rather than an article. The outcome editor flows into as many
columns as fit, the drop zone sits beside the parser's warnings instead of
above them, and the twenty-six courses of a full semester lay out seven
across. Long prose is kept readable by capping the paragraphs, not the page.

The full workspace — attainment, PSOs, surveys, trends, exports, sign-off — is
unchanged and still served at **`/static/workspace-full.html`**; every API route
behind it is live. It is parked, not deleted, because mapping accuracy is the
thing being sharpened right now and a single-purpose screen is where a wrong
cell is visible.

## What runs automatically

Open the UI and a complete worked example is already computed, stored, and
signed off ready. Nothing below asks you to author anything first:

| Step | Automatic | Override |
| --- | --- | --- |
| Course outcomes | **Detected from an uploaded syllabus**, in either AKTU format | Edit, add, or delete any outcome |
| Non-compliant CO text | Rewritten into measurable form, shown as a diff | Accept, edit, or keep the original |
| CO quality | Checked live against the 8-question test | Advisory — never blocks you |
| CO → PO competency | Inferred as 0 / 1 / 2 from the outcome text and verbatim PO statements | Any cell; overrides show blue, proposals purple |
| PO averages | Both denominators computed, rounding shown beside the mean | Change a cell; the document follows |
| CO → PSO | Inferred from the department's own PSO text | Same as PO cells |
| PSO quality | Checked for the "this is really a PO" failure | Advisory |
| Mapping level | Derived by Rule 1 → Rule 2 | Change the competency and it recomputes |
| Question → CO | Each question read and matched to the outcome it assesses | Change any tag before applying it |
| Question paper | Read from `.docx`, including choice groups and mark allocations | Edit any question or its marks |
| Marks layout | Roll and question columns detected from `.xlsx` or paste | Fix the question/CO/max table by hand |
| Choice-question bias | Both attempt policies computed, the spread reported | Pick a policy; it recomputes |
| Attainment | Computed on every change | Both SAR 3.7.2 target modes, thresholds configurable |
| Indirect assessment | Survey written from the outcomes, scored, blended | Weights, scale, and every response |
| Criterion 8 actions | Drafted from the evidence | Every draft is an editable textarea |
| Trends | Multi-year series, aligned across the framework change | Targets configurable |
| Persistence | Every run stored with its inputs | — |
| Audit trail | Hash-chained, verified on read | — |

The rule the design follows: *no screen asks a human to author anything.*
Every screen arrives with a defensible default already computed, and
disagreeing is one click.

## The three gaps that made a SAR incomplete

These were not missing features; they were places where the numbers were
arguable or the tables had holes.

**Indirect assessment.** NBA wants direct *and* indirect attainment. The
instrument is generated from the outcomes (`engine/indirect.py`), scored, and
blended. Three decisions a peer team will query, made explicitly:

- A 5-point scale runs 1–5, so the floor scores **0%, not 20%** — `(mean − 1) ÷
  (max − 1)`. The obvious alternative flatters every survey ever run.
- Indirect percentages convert to attainment levels through the **same
  thresholds** as direct attainment, so "level 2" means one thing throughout
  the report. Blending is refused outright if the two sides are on different
  scales — mixing a percentage with a level produced `PO1 = 12.67`, which
  looks like an answer.
- **Response rate is part of the result**, not a footnote. Eleven replies from
  a class of 120 is reported as `insufficient`.

**PSO columns.** Every SAR articulation table carries them and they were
empty. PSOs cannot be seeded — they are written per department — but a PSO set
is self-describing: weighting each term by how few PSOs contain it isolates
what distinguishes PSO1 from PSO2, so inference works the moment a department
types them. The checker also catches the most common peer-team finding: a PSO
that is a restatement of a PO.

**Choice questions.** "Attempt any two of five" is not absence. Both look like
a blank cell and they mean opposite things: an absent student was never
measured; a present student who skipped a question chose not to answer it.
A CO evidenced only by skippable questions is measured on a self-selected
subset — the students weakest on it are exactly the ones missing — so it reads
high. The engine computes **both policies and reports the spread**, because a
college should pick one knowing what the other would have said.

## Evidence, not recomputation

The question a peer team actually asks is *"how did PO3 become 2.14?"*, and
"re-run it and see" is not an answer — it proves what the software does today,
not what it did when the SAR was signed.

- **Runs are stored** with their inputs, thresholds, and results. An evidence
  bundle reads stored rows and never recomputes, so a 2026 bundle still reads
  as it did in 2026.
- **The audit log is hash-chained.** An altered or deleted row breaks the
  chain and `verify_chain` names the first break. This does *not* make the log
  unforgeable — anyone who can write the database can rewrite the whole chain,
  and that is not claimed. It makes the realistic failure detectable: a row
  quietly adjusted the week before a visit.
- **Overrides keep their provenance**, so the bundle can say which cells are
  the department's judgement rather than the engine's proposal.
- **Sign-off is recorded**: faculty submit, HOD approves, coordinator locks.
  Locking is terminal — unlocking is a separate coordinator action requiring a
  stated reason, so a figure cannot move as a side effect of an ordinary edit.

## The mapping rules, exactly as specified

**Three scales appear on this page and they run on different ranges.** Reading
one as another is the easiest way to get a matrix wrong, so the UI carries a
card naming all three:

| Number | Range | What it is |
| --- | --- | --- |
| **Competency** | **0 – 2** | your judgement — the only one a human sets |
| **Action level** (CL, PL) | **1 – 3** | how demanding the verb is. No 0: every outcome does *something* |
| **Mapping level** | **0 – 3** | the derived result that reaches the SAR; 0 is drawn as `–` |

**Competency is 0 / 1 / 2**, not none/partial/strong — the same scale as the
guidelines and as the matrices faculty already fill in on paper. The names
remain accepted on input so stored data keeps working.

**Rule 1 — competency matching**

| Competency | Mapping level |
| --- | --- |
| 0 — no match | 0, rendered `-` |
| 1 — partial | 1 |
| 2 — strong | defer to Rule 2 |

**Rule 2 — action level** (strong matches only). CL is the outcome's action
level from Bloom's (K1–2 → 1, K3–4 → 2, K5–6 → 3); PL is the PO's.

- `CL ≥ PL` → mapping level **3**
- `CL < PL` → mapping level **CL**

The second branch assigns **CL, the outcome's own level — not PL.** It is easy
to get backwards and this codebase had it wrong: a K2 "Explain…" outcome
against a PL3 design PO maps at **1**, not 3, because 1 is what the outcome
actually demonstrates. Assigning PL lets a weak outcome inherit the strength of
whatever it is mapped against, inflating exactly the cells a peer team reads
hardest. `test_mapping_rules.py` pins every CL×PL combination.

### What the competency inference actually scores

The rules above are exact and pinned. Their *input* was not: the competency
lexicon had never been scored against anything, so "accurate" was an assertion.
It is now measured the same way the tagger is — **48 labelled outcomes, 528
CO×PO cells, ten courses, dev/holdout split by course** (`python -m
app.engine.copo_eval`, floors in `test_copo_eval.py`):

| | dev | held-out |
| --- | --- | --- |
| Cell accuracy | 89.8% | 88.1% |
| **False-strong rate** | **7.4%** | **6.2%** |
| Strong recall | 83.3% | 60.0% |
| Mapping precision | 69.5% | 70.4% |
| Mapping recall | 93.2% | 90.5% |

**False strong** — competency 2 where the truth is 0 or 1 — is the metric that
matters, because a strong match runs Rule 2 and normally lands on level 3. A
spurious 3 that gets bulk-approved reaches a SAR; a missed strong is the same
cell proposed at 1, which a human bumps in one click. The first measurement put
it at **17.6%**, and fixing what that exposed brought it to 6.7%:

- **Subject matter was being read as the action verb.** "Express the concept of
  VLSI *design*" scanned the first six words, found `design`, and inferred K6 →
  CL 3 → a strong level-3 claim on the design PO. `leading_action_levels()` now
  reads only the verb that opens the outcome and any conjoined with it.
- **The Bloom ceiling was extended to problem analysis.** "Identify the rules
  and safety practices" is K1 and cannot be a strong analysis match however many
  signal terms it contains.

Two more came out of running the real AKTU PDF through the finished UI, and both
cost precision rather than safety:

- **Lexicon terms matched inside longer words.** `present` (Communication) fired
  on "how to *rep*resent drone systems"; `test` (Investigation) on "the la*test*
  standards". Terms now anchor to the start of a word, with the right edge left
  open so `communicat` still reaches *communication*.
- **`present` is an adjective as often as a verb.** "present day technology" is
  not a communication outcome. It moved out of the content lexicon into the
  verb table, so it counts only when it is the outcome's action — "Present the
  findings…" still maps, and still maps strongly.

Together those took held-out precision from 63.5% to 67.3% and cell accuracy
from 85.0% to 86.2%, moving dev the same direction, with recall and the
false-strong rate unchanged.

### Recall: the misses were zeros, not near-misses

At that point held-out recall sat at 78.6%, and lowering the partial threshold
did nothing for it — dev recall rose and held-out recall stayed flat at 78.6%
while precision collapsed. Every held-out miss scored **exactly 0.0**: the
engine had no term for the thing the PO statement is literally about.

Three vocabularies were missing outright, and each was written from the
verbatim PO and WK text rather than from the corpus:

- **PO6** reads "societal and environmental aspects ... impact on
  sustainability"; WK5 names environmental impacts and WK7 public safety and
  sustainable development. `ecosystem`, `biodiversity`, `pollution`,
  `emission`, `climate`, `natural resource` are instances of that and none
  were present. An outcome about the impact of engineering on biodiversity
  matched nothing.
- **PO7** ends "adhere to national & international laws" — no term for
  `regulation`, `legislation`, `statutory` or `licens`. The bare word `law`
  is still deliberately absent: it belongs to the first law of thermodynamics
  as often as to the statute book, and a test pins that.
- **PO11** reads "adaptability to new and emerging technologies" and
  "technological change" — `evolution`, `emerging`, `next generation` and
  `technological change` were all missing.

Those three alone took held-out recall to 85.7% and *lowered* the false-strong
rate. The fourth gap was harder.

### Naming the topic is not demonstrating the competency

PO1's WK4 is "engineering specialist knowledge ... bodies of knowledge for the
accepted practice areas in the engineering discipline", so discipline nouns —
`semiconductor`, `antenna`, `protocol`, `modulation` — are legitimately PO1
evidence. Adding them to the lexicon took held-out recall to 83.3% and strong
recall from 56% to 64%, and took the **false-strong rate from 6.7% to 15.8%**.

Domain nouns stack. Four of them in one sentence cleared the strong threshold
on subject matter alone, so "Examine semiconductor memory, transistor logic and
antenna behaviour" became a *strong* engineering-knowledge claim on nothing but
topic words. Capping their combined contribution was tried and only reached
10.5%.

What works is a split, and it is the honest reading of the distinction: naming
the topic says the outcome is **inside** the discipline — a partial match — and
says nothing about whether it demonstrates the competency. Specialization nouns
are now scored separately and **can never reach strong**; only the competency
vocabulary (fundamentals, principles, theory, first principles) can. Both
tests are in `test_copo_eval.py`.

That took the held-out numbers to their current values: recall **78.6% →
90.5%**, cell accuracy **86.2% → 88.1%**, strong recall **56% → 60%**, and the
false-strong rate **down** to 6.2%. Every metric improved on both splits.

A caveat on the specialization list: it is ECE- and CS-leaning because that is
what the labelled corpus covers. It is a floor to extend per discipline, not a
claim to completeness, and a department in another branch should expect to add
to it.

Strong recall is deliberately left low rather than tuned up. The 83% / 56% gap
between splits is real generalisation error on 48 outcomes, and closing it by
loosening the strong threshold trades a one-click miss for the failure mode the
whole engine is calibrated against. Two attempts are recorded as negative
results in `competency.py`: a lead-verb weight took false strongs from 9.1% to
28.9%, and removing verb/content double-counting drove them to zero while
collapsing strong recall from 83% to 47%.

The gold labels are our own reading of each outcome against the verbatim PO
statements. Published departmental matrices were deliberately *not* used as
gold — they over-map, and `test_gold_matrices_are_not_over_mapped` fails if any
labelled outcome claims more than four POs.

## The PO averages, and which denominator

The average row is the line a peer team reads first, and it is the one
institutions most often compute inconsistently. There are two defensible
denominators and they give different numbers:

- **Avg (mapped)** — the sum of the levels over the COs that actually map to
  that PO. This is the AKTU/NBA articulation-matrix convention and the figure
  carried into SAR Table 1.4.2.1.
- **Avg (all COs)** — the same sum over every CO in the course, counting an
  unmapped cell as 0.

A PO touched by one CO at level 3 averages **3.00** the first way and **0.60**
the second across five COs. Reporting one without saying which invites "out of
how many?", so both are in the document with the denominator printed beside
them (`4/5`).

Three more decisions that are visible rather than silent:

- **Nothing rounds quietly.** The mean is shown to two decimals and the rounded
  whole number sits next to it, because some templates want the integer and a
  reader has to be able to see it was 1.67 before it became 2.
- **An untouched PO has no average, not a zero.** `0.00` reads as "addressed
  and attained nothing". A PO no outcome touches prints a dash, and the note
  names every such column.
- **The level distribution is printed.** `1×1, 1×3` and `2×2` both average
  2.00 and they are not the same matrix.

The document is one download in **Word, Excel, CSV or Markdown**, and carries
three tables: the articulation matrix with its average row, the PO averages,
and — because a reader cannot check a mapping level without it — the Bloom's
levels behind them, each labelled as declared by the syllabus or inferred from
the verb.

The browser renders the engine's numbers rather than computing its own, so the
panel on screen and the downloaded file cannot disagree.

## Giving an outcome a verb that can be assessed

Two faults, both fatal to assessment, are flagged in the editor with the
measurable verbs ranked for **that sentence**, one click to apply.

**A vague verb.** The guidelines name six to avoid — *know, understand, learn,
gain knowledge, be familiar with, appreciate* — and thirteen to prefer.
"Understand the fundamentals of IoT" cannot be assessed: nothing a student
does would settle whether they understood.

**No verb at all.** "Application of IoT in Industrial and Commercial Building
Automation.", "Designing of logic gates.", "Building State of the art
architecture in IoT.", "Interface mechanism of different Sensors…" — these
name the activity as a *noun*, so there is not even a wrong action to assess.
Three of the five outcomes in one AKTU IoT elective are written this way.

Here the author's own noun is the evidence: whoever wrote "Application of"
meant *apply*, so that verb ranks first, the same way a verb buried behind a
vague opener does. The conversion is grammatical, not a prefix — "Apply IoT in
Industrial and Commercial Building Automation.", never "Apply Application of
IoT…". A bare noun phrase gets its article: "Interface mechanism of X" becomes
"Explain **the** interface mechanism of X".

The substitution looks trivial and is not:

**The verb decides the mapping level.** It sets the CO's Bloom level, Bloom
sets CL, and under Rule 2 a strong match with CL < PL maps at CL. Swapping
"Understand" (K2, CL 1) for "Design" (K6, CL 3) turns every level‑1 cell in
that row into a level‑3 cell. So the ranking prefers a verb at the level the
syllabus already declared — those move nothing — and any option that *would*
move the action level is drawn dashed and says so on hover. Applying a verb
also sets the Bloom's level to match, because adopting the word without its
level leaves CL saying one thing and the sentence another. The badge then
reads **K set by you**, which is neither "declared" nor "inferred".

Three things the rewrite gets right that a blind substitution does not:

- **The preposition goes with the phrase.** "Gain knowledge **of** optical
  fibres" → "Explain optical fibres", not "Explain of optical fibres".
- **"How" is an object, not a preposition.** "Understand **how to** represent
  drone systems" → "Explain how to represent drone systems".
- **A verb hiding behind the vague one wins.** "Learn **to solve** numerical
  problems" is not missing an action verb — it is hiding one. It becomes
  "Solve numerical problems", not "Apply to solve numerical problems". No
  heuristic beats the author's own word.

A verb that repeats the word after it is demoted, because "Design the design
requirements" reads like a machine wrote it.

**Precision is the whole game.** Telling a faculty member their working
outcome is broken costs the tool its credibility on the first screenful, so
the verbless rules are deliberately narrow: a recognised nominalisation, a
gerund, or an unrecognised head word followed by an abstract noun and a
preposition. Verbs the Bloom tables happen not to list are recognised too, so
"Analyse and design basic digital IC circuits" (British spelling), "Appraise
the steady state error" and "Illustrate the programming model" are left alone.
The preposition is what separates the two: **"Design of logic gates"** is a
heading, **"Design a low pass filter"** is an outcome. Across the 189 outcomes
in the three published schemes under `backend/tests/fixtures/`, this flags 26
— 17 vague, 9 verbless — and nothing else, and both directions are asserted in
`test_verb_advisor.py`.

### The recommendation needs no API key

`recommended` on `/api/verb-advice` **is** the answer, and it is computed
locally: the guideline lists, what the author already wrote, what the syllabus
declared, and a subject-matter lexicon. It carries the verb, the reason it came
first, the rewritten sentence, and the runner-up — plus `clear: false` when the
top two score alike, because saying "either is defensible" is more useful than
implying the first was chosen on evidence.

`ai` is a **second opinion, never the answer**. With `ANTHROPIC_API_KEY` set,
the model is asked which of the thirteen fits and its pick is shown with its
reason; a verb outside the list is rejected rather than displayed. Without a
key the field is null, nothing on the page is missing or greyed out, and the
deterministic ranking stands alone — the same discipline the question tagger
uses.

## Reproducing the department's own mapping workbook

The department supplied `All CO-PO Mapping 2024-25.xlsx` — 44 course blocks
across three sheets, hand-built — and asked for output that looks like it.
Everything below was measured from that file rather than assumed:

| Measured | Value |
| --- | --- |
| Columns | PO1…PO12 then PSO1, PSO2 — **GAPC v3**, so the page opens on v3 |
| Levels used | only 1, 2, 3. An unmapped cell is **blank** — never 0, never "–" |
| Average denominator | **mapped cells only** (23 discriminating cases, 0 counter-examples) |
| Block shape | course in the corner cell, title repeated on the average row |
| PO12 coverage | **92.1%**, 191 of 214 outcomes at level 3 — the fullest column of the fourteen |
| PO1–PO5, PSO1, PSO2 | 85% each, and every outcome of every engineering course |
| PO6–PO11 | 10–19% — plainly judged case by case |

`engine/copo_document.course_block_table` emits one block in that shape and
`engine/office_export.course_blocks_to_xlsx` stacks many down a sheet;
**Whole file as a mapping workbook** on the averages card writes every course
in the uploaded syllabus in one pass. Row-for-row against their Digital
System Design block, the generated file is identical.

### Their sheet is a template plus the rules, and the software says so

Two things in that sheet are not inferences. PO12 at 92% and PO1–PO5 at 85%
are the department asserting facts about its own programme — no reading of
"Recognize the need for … lifelong learning" against a CMOS-layout outcome
will ever produce them, and tuning the scorer until it did would be
dishonest. So they live in `engine/conventions.py`, off the inference path
entirely, under rules that are the point of the module existing:

- **A convention sets competency; Rule 1 and Rule 2 still set the level.**
  That is what reproduces a hand-built matrix instead of flattening it. In
  their Digital System Design block the four design outcomes map PO3 at 3 and
  the one "Perform … simplification" outcome maps it at **2**, because CL 2 is
  below PO3's PL 3 — same column, same convention, different level.
- **It only fills what the engine left empty**, except where you explicitly
  ask it to beat a *weaker* reading. Lowering an inference is refused outright,
  and every raised cell carries `raised_from`, counted on the matrix summary.
- **It is stated once, not annotated everywhere.** The matrix and the
  exported headers are the department's own document reproduced, so a
  convention cell looks like any other cell; half the columns flagged with a
  dagger is an annotation on their sheet rather than a copy of it, and it
  reads as a defect where none exists. Where a cell came from is in the
  methodology note, in the matrix summary (`From your columns — n, k read
  weaker in the text`), and in the cell's own panel when you click it.
- **`competency.py`, `mapping_rules.py` and `copo_eval.py` never import it**,
  which a test enforces, so the measured accuracy stays a measurement of
  inference alone.

The page applies both and offers no switch for them. They are the department's
standing decision about its own programme, not a per-session choice, and
without both the matrix stops being the sheet the department asked to have
reproduced. The **server** default is still off — `ConventionConfig.enabled`
and `CoreConfig.enabled` are `False`, so nothing is applied unless the request
says so, and the API is usable either way.

### PSO columns

`PSO1` and `PSO2` are columns everywhere — matrix, averages, workbook — the
moment the department types the statements. **AutoOBE will not write them.**
A PSO with no statement has no column, because a column nobody can point to a
statement for is not defensible in front of a peer team, and a placeholder
that ships as a default eventually gets signed.

This is not a scruple; it changes the numbers. A PSO's PL is read from the
leading verb of the statement, exactly as a PO's is, and Rule 2 then uses it.
Against their Digital System Design block, invented statements led by *Design*
and *Apply* scored **38/40** — the two PSO cells wrong. The department's real
pairing, PSO1 led by *Apply* and PSO2 by *Design*, scores **40/40**.

### Mapping it the way a person would

Two later corrections came from the department looking at the output and
saying it did not read like human judgement. Both are applied *through* the
rules rather than around them.

**Modern tool usage is a moderate contribution, not a substantial one.** PO5
opens "**Create**, select, and apply … modern engineering and IT tools" —
create is K6, the highest of the three and the culminating action, so PL 3,
exactly as PO3 gets PL 3 from "Design solutions". Rule 2 then differentiates
per outcome instead of printing one number down the column:

| the outcome | CL | PO5 |
| --- | --- | --- |
| "Perform numerous arithmetic and logic simplification…" | 2 | **2** |
| "Design various ADCs and DACs according to the given specifications." | 3 | **3** |

Nothing special-cases PO5; it is the same mechanism that already made PO3
differentiate, and PO1 — which only ever asks the graduate to "Apply" — is
untouched. It costs parity with their 2024-25 sheet, which records PO5 flat
at 3: that column falls from 78.5% to 44.0% and the six-column figure from
83% to 76.8%, with **every other column unchanged**. The newer instruction
outranks the older artefact, and a test asserts the other columns held so the
lowered floor cannot absorb a real regression later.

**A lab, a project and a humanities course do not share a column set.** One
setting for a whole workbook is what puts engineering knowledge against Rural
Development. Their own sheet splits four ways, and the split is total rather
than a tendency:

| kind | outcomes | columns, from their sheet |
| --- | --- | --- |
| theory | 100 | PO1–PO5 on 100% of rows, PO6–PO11 on 0% |
| lab | 64 | the same shape, stronger within it |
| project / internship | 25 | PO1–PO5 **plus PO10** on 76% — the report and the viva |
| non-technical | 25 | PO6–PO11 only, PO1–PO5 on exactly 0 rows |

`conventions.classify_course` reads the title first, then the L-T-P triple as
the tiebreak for a lab that does not say "Lab". It gets **44 of 44** of their
courses onto the right half, including the five whose course code the parser
never recovered and the two that read both ways: *Project Management and
Entrepreneurship* is a management course, and *Mathematics-III* is a technical
one despite sharing the BAS/KAS prefix with *Technical Communication*. The
PSOs join a technical course and not a humanities one, again their practice.

It is a judgement about the course, so it is reported rather than applied
silently: the matrix corner names it, the note beneath spells out the columns
that follow, and the workbook toast says what the mix was ("12 course blocks
(6 theory, 6 lab)").

### The action level of every PO, audited

Two POs were corrected one at a time; the department then asked for all of
them to be gone through. Every statement was read for the action it actually
demands of the graduate, the reading was checked against the 214 rows of their
own sheet, and both the answer and the check are recorded in the seed file
next to the statement. Four levels moved:

| PO | the verb that decides it | was | now | against their sheet |
| --- | --- | --- | --- | --- |
| PO5 Modern Tool Usage | "**Create**, select, and apply" — K6 | 2 | **3** | 78.5% → 44.0%, accepted (above) |
| PO8 Ethics | "**Apply** ethical principles" — K3 | 1 | **2** | 20.0% → **73.3%** |
| PO9 Individual and Team Work | "**Function** effectively as … leader" | 1 | **2** | 39% → **69%** |
| PO10 Communication | "**Communicate** … write … make" — K3 | 1 | **2** | 50% → 55% (9.5% → 66.7% where it is populated) |

PO8, PO9 and PO10 had all been sitting at PL 1 — a blanket assumption that the
non-technical outcomes live at the bottom of the scale, which none of the three
statements says. That one group is the largest correction in the framework.

Four candidates were rejected *because* of the check, and the rejections are
worth as much as the changes:

- **PO6 → 3.** "Apply reasoning … *to assess* societal issues": the action is
  *apply*, and "to assess" is a purpose clause, the same construction as PO4's
  "design of experiments". 83.3% at PL 2 against 53.3% at PL 3. But v4 restates
  the same attribute as "**Analyze and evaluate** societal and environmental
  aspects", which genuinely is PL 3 — so v3 keeps 2 and **v4 moves to 3**. The
  frameworks differ because the sentences differ.
- **PO7 → 1.** "Understand" is K2, but the second predicate "**demonstrate** the
  knowledge of" is K3 and is the higher of the two. 65.4% at PL 2, 30.8% at PL 1.
- **PO2 → 3.** A Bloom table returns K6 for "formulate", but here *formulate*
  means to state the problem precisely — a stage of analysing it, in a PO titled
  Problem Analysis that culminates on *analyze*. Their sheet is decisive: PO1 and
  PO2 carry byte-identical columns in all 214 rows.
- **PO11 → 3.** "manage projects" reads higher than *apply*, but 57.5% at PL 2
  against 52.5% at PL 3.

After the audit **PO12 is the only PL 1 left in v3**, from "Recognize the need
for" — which is why the life-long learning convention yields a full column of
3s by feeding the rules rather than by asserting anything.

#### Three bugs the audit found in the verb reader

Auditing the POs meant running the CO machinery over twelve carefully-worded
sentences, which is a better test than any fixture:

1. **A second verb with no separator is a noun.** "Communicate design decisions"
   read K3 *and* K6, and the K6 put a reporting outcome on the design PO at
   level 3. Real conjoined verbs always carry a separator — "Describe, analyze
   and design" — so the separator is now required, and commas are kept as
   tokens rather than stripped.
2. **`", and analyze"` lost its verb.** The comma matched, consumed the word
   "and", and the actual verb was never seen. That verb is the whole of PO2.
3. **Compound nouns after a conjunction.** "write effective reports **and design
   documentation**" is one noun phrase; reading it as an action is what held PO10
   at PL 1. Now guarded by a closed list of the heads these documents actually
   use — "and design circuits", "and design a system" are untouched.

None of the three moved the accuracy floors (88.1% cell, 6.2% false-strong):
they fire on wording the labelled corpus happens not to contain, which is
exactly why twelve hand-written PO statements found them.

### Filling only the columns that are earned

"It is not necessary to fill all the columns till PO5 — if they really needed
to be filled then only fill them, else let them be as they are."

The core-columns convention no longer asserts five columns for every outcome.
It still says *which* columns this department maps, but each cell now has to be
able to point at a word: `competency.signal_strength` scores the outcome and
its course syllabus with the thresholds removed, and a column with no trace at
all is left exactly as it was.

Digital System Design is the case it exists for. Its own outcomes evidence PO1,
PO2 and PO3; **PO5 survives only because Unit IV names Verilog RTL and three
EDA tools by vendor**; and **PO4 goes blank on all five outcomes**, because
nothing anywhere in that course speaks of investigation — it is a lecture
course, and the paired lab is where that belongs.

Two guards keep it honest:

- **A column the scorer cannot score is never vetoed.** A PSO is a statement
  the department wrote with no signal lexicon behind it, so nothing could ever
  evidence one; reading "no scorer" as "no evidence" emptied both PSO columns
  outright the first time this ran.
- **It only ever withholds a new cell, never removes one.** A reading the
  engine made stands.

It costs coverage against their 2024-25 sheet, and the trade is the point:
**1125 cells filled instead of 1752, and 70.3% agreement instead of 82.3%.**
Most of the gap is PO4 (86% → 21%) and PO5 (88% → 33%) — the two columns a
blanket was least able to justify.

Withholding a cell is the honest answer to *"does this outcome teach that?"*
and the wrong answer to *"does this programme cover that?"*, which only the
whole file can answer. So the workbook now names, on its cover, every column
that came out empty across every block — for the ECE 2nd-year file that is
PO6, PO7, PO8, PO9 and PO11, which is a real gap to close rather than a
rendering detail.

### The floor: filled at 1 rather than left blank

The department read that matrix and gave the other half of the instruction:

> "For technical subjects make sure to fill PO1–PO5. If according to you they
> are least relatable then keep them 1, but fill them."

Both instructions hold, because they answer different questions. The gate still
decides whether a cell is *earned* by the text. What changed is what an
unearned cell says. Not nothing — **1**.

Their argument is a curriculum one rather than a textual one, and it is right:
an engineering course contributes something to engineering knowledge, problem
analysis, design, investigation and tool usage whether or not its five
sentences happen to say so. A blank claims the opposite, and a peer team reads
it that way. A 1 says the honest thing — it contributes, but this outcome is
not where it is carried.

Three things keep it from collapsing back into a rubber stamp:

- **The floor is only ever the core five.** PO10 rides along on a project
  block and the department's own sheet fills it on roughly three rows in four,
  so flooring it would invent a pattern they do not have.
- **It never reaches a non-technical course**, which is given PO6–PO11 as its
  columns and has no core column for the floor to touch. This is what keeps
  "fill PO1–PO5" from meaning "fill PO1–PO5 everywhere" — the exact pattern
  that put engineering knowledge against rural development.
- **Every floored cell is marked `floored`** and says so when clicked: *"a
  technical course contributes something here, but this outcome is not where
  it is carried."* A 1 the curriculum argument put there is not the same cell
  as a 1 the outcome earned, and the matrix never blurs the two — the same
  reason `raised_from` exists.

Measured against their sheet, the floor is what closes the technical half:

| | cells of theirs we fill |
| --- | --- |
| gate only, outcome text only | 46.0% |
| gate only, syllabus read | 54.0% |
| **floor on, syllabus read** | **92.3%** |

By course kind: **theory 97.7%, lab 100%, project 94.4%, non-technical 53.3%.**
The non-technical figure was 44.3% before the parser learned to read a
`Module 1-` heading, a two-code course banner and a qualified bibliography
heading. What is left there is mostly outcomes the department wrote themselves
and AKTU never published.
`tests/test_sheet_parity.py` pins each of those separately, because 91% overall
is easy to hold while a whole class of course sits empty — which is precisely
the state they found this tool in.

### The course that scored zero on ethics

The department uploaded `AKTU_UHVPE.pdf` — Universal Human Values and
Professional Ethics — and every outcome scored **0.0** against PO8 Ethics.
*"its uhvpe syllabus it is based on human values still got 0 mapping under
ethics ... thats why analysation is most important."*

Three independent defects produced that, and each is pinned by a test against
the real file:

**1. The last outcome of the list was being dropped.** Nothing follows the
final item to stop it, so CO5 ran on into "Catalogue Description" and was then
discarded for exceeding `MAX_STATEMENT_CHARS`. The outcome lost was
*"Distinguish between ethical and unethical practices"* — the only one in the
course containing the word "ethical". Terminal punctuation now ends a wrapped
statement: PDF extraction breaks lines mid-phrase, so a genuine continuation
almost never follows a full stop and a heading almost always does.

**2. Its syllabus was never read.** This document writes units as a heading
plus prose — `UNIT-5 Implications … on Professional Ethics` — not as the
`Unit Topics Lectures` table the engineering syllabi use. Five units of human
values were invisible, including the one titled after the very PO that scored
zero.

**3. No lexicon held any of its vocabulary.** *Value education, natural
acceptance, harmony, co-existence, human-human relationship, humanistic* — an
ethics lexicon drawn only from the PO statements has none of them, and a
teamwork lexicon that knows "team" has nothing for a course about relating to
other people. Both were extended from the words these documents actually use,
checked against every technical outcome in the department's sheet for
collisions first:

| Guard | Why |
| --- | --- |
| `harmonious` and `harmony`, never the stem `harmon` | it would match **harmonic distortion**, which is in every analog syllabus |
| phrases, never bare `relationship` | "the relationship between voltage and current" is not team work |
| `value education`, never bare `value` | "the value of the resistance" |
| `self-exploration`, never bare `self` | self-bias, self-regulation, self-study |

Result on that file, against the cells the department's own sheet fills:
**13/20 from the outcome text alone, 20/20 once its syllabus is read.** PO6,
PO7, PO8 and PO9 now map for every outcome; PO10 and PO11 stay blank and the
page says why; PO1–PO5 are untouched, which needed one more fix —
`SYLLABUS_MIN_STATEMENT` went from 0.3 to 0.5 because a single incidental
"basic" was admitting the whole syllabus and proposing Engineering Knowledge
against a course on the Self and the Body.

The five other non-technical courses in their sheet reach only ~25% from
outcome text alone, because their outcomes are one-liners. That is a missing
input rather than a defect: upload each syllabus and the same mechanism
carries them.

**One last thing this file needed.** It prints its name *above* its code —

```
Universal Human Values and Professional Ethics
BVE301 / BVE401/        L T P C
```

— and that code line matches no course-header pattern, so the course reached
the workbook as "Untitled (BVE301)". The name is now recovered by position,
guarded so it can only ever pick up something that reads like a course name
and like nothing else the parser recognises.

### How close it gets

Measured against all 214 outcomes in their own sheet, holding competency at
what their template asserts:

| | |
| --- | --- |
| Whole 2024-25 sheet, columns asserted for every outcome | **82.3%** |
| Whole 2024-25 sheet, columns only where evidenced | **70.3%** — 1125 cells filled instead of 1752 |
| PO12 | **100%** across the technical courses |
| Course kind (which half of the framework) | **44 of 44** |

The second row is the one that ships. Both numbers are here because the drop
is a decision, not a regression: the department asked for judgement over
replication twice, and each step away from their sheet has been measured and
written down rather than absorbed.

Their own sheet applies the same `CL < PL → CL` cap in places — Electronic
Devices records "Describe the principles of semiconductor physics" and
"Demonstrate the working principle…" at 1 across PO1–PO5, exactly as the rule
gives, and AutoOBE reproduces both rows. It then records "Explain the carrier
transport in semiconductors" at 3. Same verb class, same course, two answers.
That is the shape of the residual below.

Setting the deliberate PO5 change aside, what is left is one thing rather than
a scatter: **outcomes at action level 1** — "Explain…", "Describe…" — that the
sheet records at 2 or 3. Rule 2's `CL < PL → CL` branch caps those at 1.

That is a disagreement inside the sheet, not between the sheet and the rule.
Electronic Devices records "Describe the principles of semiconductor physics"
and "Demonstrate the working principle…" at 1 across PO1–PO5, exactly as the
rule gives, and AutoOBE reproduces both rows. Three rows later it records
"Explain the carrier transport in semiconductors" at 3. Same verb class, same
course block, two answers. It is reported rather than tuned away, and the
department has been asked which reading it wants.

## Every AKTU syllabus ships with the tool

After the Universal Human Values case the honest report was that the fix
generalised and the *inputs* did not. Five more of their non-technical blocks
would stay thin until somebody found and uploaded five more syllabus PDFs. The
department's answer was to ask for all of them:

> "Search for all the AKTU subjects, their syllabus and their COs, then train
> this software accordingly for mapping."

So the university's syllabi ship with the tool. `tools/fetch_aktu_syllabi.py`
walks AKTU's own year-by-year index pages for the 2021-22 through 2026-27
sessions and pulls down every B.Tech document it lists — **214 PDFs, about
100 MB**. `tools/build_aktu_catalogue.py` parses them into
`app/data/aktu_catalogue.json`: **749 distinct courses, 361 of them carrying
course content**, each stamped with the document and session it was read from.
116 of those are laboratory courses, whose content is a list of experiments
rather than a unit table.

Neither script runs at request time. The catalogue is a build artefact, and
the tool is offline as it has always been.

A course in a workbook is found in it by code and title, and its content joins
the mapping exactly as an uploaded syllabus would. Three things had to be got
right for that to be safe:

- **A code survives being renumbered.** AKTU changes the series letter and
  nothing else — `KVE-301` becomes `BVE301`, `KEC302` becomes `BEC302` — so
  the match is on `VE301`, not the printed code.
- **An acronym finds its expansion.** A department writes `UHVPE`; AKTU prints
  *Universal Human Values and Professional Ethics*. The two share no word, and
  this is the course whose syllabus mattered most.
- **A short title does not match a longer, different course.** The first
  version scored on containment, which made `Cyber Security` a perfect match
  for AKTU's *Introduction to Security of Cyber-Physical Systems* — every word
  present, entirely the wrong course — and handed a department that course's
  syllabus. Both titles now have to be substantially the same; what rescues
  the genuine case (*Rural Development* against *Rural Development:
  Administration and Planning*) is the code, which is what a code is for. The
  same rule keeps a lab from standing in for its theory course, which share
  every word of their titles and none of their content.

Two limits are deliberate. **The catalogue never supplies course outcomes** —
a department's COs are their own, and they rewrite AKTU's verbs on purpose;
their `C202.3` reads *"Analyse the value of harmonious relationship"* where
AKTU publishes *"Understand the value of…"*. And **it is never silent**: the
matched code, session and source document come back with the mapping and print
above the matrix, because standing a 2021 syllabus in for a 2026 course is a
judgement the department may want to refuse, and they can only refuse what
they can see. `use_catalogue: false` turns it off; an uploaded syllabus always
wins.

Two filters keep the artefact clean, and both were found in the corpus rather
than imagined. A **numbered reference list parses as a numbered outcome list**
— one AKTU document yields a "course" whose first outcome is *"V.
Sivaramakrishna (Ed.), Cultural Heritage of India, Bharatiya Vidya Bhavan,
Mumbai"* — so bibliography shapes are dropped. And where two syllabi share a
page **one unit table runs into the next**: AKTU writes every B.Tech course in
five units, so an entry with fifteen has absorbed a neighbour. Those keep
their outcomes and lose their syllabus, because content from the wrong course
is worse than no content.

## What 214 real documents found in the parser

Reading the whole corpus was worth more than the corpus. Five defects surfaced
that no single upload had shown, each measured across all 2,100-odd courses
before it was touched.

**One outcome in four was being silently deleted.** A document that writes
`Course Outcomes:` and then `CO1 …`, `CO2 …` — rather than a bare numbered
list — opens the numbered-list reader on the heading. CO1 falls through it and
is read; CO2 then meets the terminal-punctuation rule added for the UHV fix,
which sees CO1's finished sentence, closes the list and swallows the line. The
course comes out numbered CO1, CO3, CO4, and nothing anywhere says so:
**393 of 1,455 courses, 27% of the corpus.** A line that labels itself CO2 is
now a new outcome whatever list state the reader thinks it is in. **393 → 5.**

**"Module" is a unit.** 241 headings across 18 documents write `Module 1-…`
instead of `UNIT-1 …`, including both non-credit papers. Requiring the literal
word UNIT left those courses with no content at all — the same failure the UHV
syllabus had, in a different spelling.

That fix immediately exposed the next one. A course whose units ran I, II,
III, IV, V and then 1, 2, 3 is not a course with eight units; it is two
courses whose boundary the header reader missed, and eight modules of Go
Programming were being filed under Adaptive Signal Processing. **A change of
notation ends the sequence** — and that alone took the number of courses with
a run-on unit table from 18 to zero.

**A course can be introduced by two codes.** `BNC501/ BNC601- CONSTITUTION OF
INDIA` matched no header pattern, and worse, the all-caps banner guard then
read it as a department letterhead and threw the line away. Both of the
department's non-credit courses arrived untitled because of it.

**A bibliography heading can carry a qualifier.** The terminators knew "Text
Books" and "Reference Books" and neither "Suggested Text & Reference Books"
nor "Suggested Textbooks / Reference Books" — 169 headings across the corpus,
each one a reference list left running as though it were an outcome list.

**A lab's syllabus is its experiment list.** Every laboratory course reached
the scorer with no content whatsoever, which matters more for a lab than for a
lecture course: the two POs a lab most obviously serves are Conduct
Investigations and Modern Tool Usage, and neither is evidenced by *"Understand
working of PN junction diode"*. The experiment list behind that outcome names
a CRO, a multimeter, a function generator and the graphical measurement of
static and dynamic resistance. On BEC351 Electronic Devices Lab the cells
filled at the floor rather than earned fell from **18 to 5**.

It is read as course content and nothing else. *"Write a program to
demonstrate the working of the ID3 algorithm"* is a task a student performs in
one session, not a statement of what the course achieves, and the list ends
where the CO list begins.

## The columns a course is allowed to reach

The scorer reads one sentence at a time and has no idea what course it belongs
to. *"Differentiate and relate the functioning of Indian parliamentary system
at the centre and state level"* contains **differentiate**, which is a Problem
Analysis verb wherever it appears — so the sentence proposes PO2 at competency
2, on a course about the Constitution of India.

The department's sheet maps PO1–PO5 on **zero** of the 25 outcomes across
their six non-technical courses, and a peer team opening the file to find
Problem Analysis against Constitutional Law is the exact failure this is all
meant to prevent. So on a non-technical course the **course kind wins over the
sentence**: `withhold_core_columns` takes the engineering columns back, and
every withheld cell is returned and named in the matrix note rather than
quietly dropped.

The reverse is deliberately not done. A technical course stays free to reach
PO6–PO11 where its text earns it — an Environmental Engineering course
genuinely does, and the 0% in their sheet is a fact about ECE syllabi rather
than a rule about engineering.

## Reading the course syllabus, not just the outcome

A course outcome is one sentence. The unit-wise syllabus above it *in the same
document* is a page, and it is the only place that says what the course
actually teaches. Digital System Design's CO4 reads "Analyze various logic
families and design circuits using PLDs" — nothing there evidences the
modern-tool-usage PO. Unit IV of the same course names "Verilog RTL coding"
and "Siemens EDA Precision RTL / Cadence Genus / Synopsys Design Compiler".

So `syllabus_parser` now reads the unit table as well as the CO table. It is
recognised by the three column headings every AKTU course block opens with
("Unit Topics Lectures"), read to the first of *Text/Reference Books* or
*Course outcomes*, and ended there — reference lists and the CO list itself
must never leak into the course content. The header repeats a dozen times in a
whole-semester PDF, so it has to be matched **before** the running-header
filter classifies it as page furniture; that one ordering was the difference
between 84 units and none. Lab courses list experiments rather than units and
correctly come back with no content at all.

The scorer treats it as corroboration and never as substitution, under three
rules that are arithmetic rather than intent:

| Rule | Why |
| --- | --- |
| each syllabus term counts for a **quarter** of its weight | a page matches far more terms than a sentence; at full weight it drowns the outcome it is meant to support |
| the total is capped **below** the partial threshold | course content can map nothing on its own — 0.6 against a threshold of 0.7 |
| the outcome's own text must already point at that PO | a course that teaches simulation does not make every one of its outcomes a tool-usage outcome |

Promotion to a *strong* match additionally needs the sentence alone to have
reached the partial threshold, so the outcome carries the claim and the
syllabus only confirms it. The two kinds of evidence are reported in separate
fields and rendered in separate blocks, so a reader can see which half of the
case came from where.

Measured on the 14 courses where the department's sheet and a parsed syllabus
overlap, with conventions off — the honest path, what the text alone supports:

| | text only | with course content |
| --- | --- | --- |
| cells agreeing with their sheet | 47.3% | **49.2%** |
| presence recall | 17.1% | **20.5%** |

With the departmental conventions on it changes nothing, because those columns
are asserted anyway — which is the point. It improves the matrix you get when
you *don't* assert them. Passing no syllabus reproduces the previous scoring
exactly, which is what the accuracy evaluation measures, and a test pins that.

## Reading the Bloom level off the verb

Three things stood between the verb scanner and the verb, each worth measuring
rather than assuming. Fixing them took agreement with the department's own
matrix from 67.8% to 83.0% while leaving every accuracy floor untouched:

- **"Students will be able to design…"** stopped the scan dead on *students*
  and the outcome fell back to the K2 default. 45 of their 214 outcomes carry
  a preamble; 17 landed on the wrong action level because of it.
- **"Analyse", "Distinguish", "Determine", "Appraise"** were absent from the
  Bloom tables. Reading *Analyse* as K2 because the table spells it *analyze*
  is a spelling bug wearing a taxonomy costume.
- **"Effectively write the report"** — an adverb is never the action.

That last one nearly cost more than it fixed. A `[a-z]+ly` pattern also strips
**Apply**, the commonest verb in an AKTU outcome, flooring every "Apply the
concept of…" row at CL 1; it cost 4.4 points before it was caught. The rule is
now a closed list and there is a test named after it.

**PO4's action level was wrong**, and their matrix is what proved it. Its
statement asks the student to *use* research-based knowledge — Apply level,
PL 2 — and the only "design" in it is the noun phrase "design of experiments",
which an earlier reading mistook for the PO's own action. With PL 3 that column
scored 42.9% against their matrix; with PL 2 it scores above 80%. PO3 remains
the design PO at PL 3.

## Reading a real syllabus

Drop a `.pdf`, `.docx`, or `.txt`. **Both AKTU outcome formats are read:**

1. **A CO/KL table** — `CO1 | Explain … | K2`.
2. **A numbered list under a "Course Outcomes:" heading**, with no `CO`
   prefixes and no K-levels at all. This is the newer scheme's format, and the
   parser originally found *nothing* in such a document. A syllabus is full of
   numbered lists — text books, references, lab experiments — so the list is
   read only after the heading and only until the next section.

A whole-semester AKTU PDF holds a dozen courses that all number their outcomes
from CO1, so the parser segments by course, taking the code, title and L-T-P
from the per-course banner (`BEC701 VLSI Design 3L:0T:0P 3 Credits`). If a
course header is missed, a repeated CO code starts a new block rather than
merging — silently fusing one course's outcome with another's would be
invisible in the output, which is the worse failure. Running headers and page
footers are skipped rather than treated as list terminators, because a
five-item outcome list routinely straddles a page break.

### Course banners the page layout broke

The single biggest source of "Untitled" courses is not an unusual header
*shape* — it is a header the PDF's column layout **split across lines**. Each
half then matches nothing, so the course arrives with no code and no title,
*and* the orphaned lines get swallowed into the previous course's last
outcome. Across three real AKTU schemes this accounted for almost every
failure. Four distinct breaks occur, sometimes stacked:

```
BEC-                          BAC                      BAC-653A WIRELESS COMMUNICATIONS
502 MICROPROCESSOR 3L:1T:0P   052 REMOTE SENSING 3L:0T:0P   NETWORKS LAB 0L:0T:2P 1 Credit

BEC-            <- the code split, then the title wrapped twice
051
IoT - ARCHITECTURE, COMMUNICATION,
TECHNOLOGY & ITS APPLICATIONS 3L:0T:0P 3 Credits
```

The reflow is **anchored on the L-T-P marker**: lines are only rejoined when
the continuation actually completes one, within three lines. That is what
keeps it safe — ordinary prose that happens to start with capitals is never
touched, and `micro-` / `processor` stays split. It runs on pasted text too,
because text pasted into the box is usually copied straight out of a PDF.

**A Unicode hyphen is still a hyphen.** The 2025-26 fourth-year scheme writes
four of its six course codes with `U+2010 HYPHEN` and two with the ASCII one;
only the ASCII two were ever recognised. The hyphen family is normalised, and
an en dash is normalised *only* when it sits inside a course code — elsewhere
it is real punctuation ("IoT – Architecture").

Measured on the three published schemes in this repository:

| Scheme | Courses | Identified before | after |
| --- | --- | --- | --- |
| ECE V & VI, 2025-26 | 26 | 10 | **25** |
| ECE VII & VIII, 2025-26 | 6 | 2 | **6** |
| ECE VII & VIII, 2026-27 | 6 | 6 | **6** |

The one that remains genuinely has no banner in the extracted text: its page
opens on "SUGGESTIVE LIST OF EXPERIMENTS" with the course name nowhere near.

### Running headers inside outcome text

"ELECTRONICS AND COMMUNICATION ENGINEERING" heads all thirty pages of a real
scheme. It is a department name, not a page number, so the page-furniture
patterns missed it — and it was appended to whichever outcome was open when
the page turned. One course's CO5 read:

> Interpret faults in digital circuits, Fault Models and various Testing
> Methodologies. **ELECTRONICS AND COMMUNICATION ENGINEERING.**

That text then fed the CO → PO mapping. Rather than add another pattern per
university, the document is asked which of its own lines repeat: a line
appearing three or more times is furniture. Numbered items and CO rows are
excluded from the count, because two electives in one scheme legitimately
share an outcome and no amount of repetition makes a real outcome into
furniture. A test asserts that no outcome in any of the three real schemes
carries page furniture.

### A rescue that was removed

Recovering the course name from the line above the outcomes sounded obvious
and was tried. On the three real schemes it fired five times and was **wrong
five times** — it adopted "P. K. Champati Ray, Wiley, 2022", "Edition, Penram
International Publication", "VHDL PROGRAMS" and the running page header as
course names, because the lines directly above "Course Outcomes:" in a real
scheme are the reference list. A wrong course name is worse than none: it
reaches the SAR looking authored. It was deleted rather than tuned.

### When a course header is not recognised

A whole-semester PDF holds a dozen courses, and every one of them needs its own
identity. When a header line is not recognised the outcomes still parse — they
just arrive with no code and no title, and the UI shows a card reading
"Untitled". Four of those on screen and a reader cannot tell which course is
which, so a missed header costs far more than it looks.

Nine header shapes are pinned by test, including the ones that vary by
university: `KCS-501`, `BCS 501`, `MTCS1011`, a labelled `Course Code:` /
`Course Title:` pair, a flattened table row, and the L-T-P written as
`3L:0T:0P`, `3-0-0`, or after the credits. Four real defects were found by
writing those tests:

- **An ALL-CAPS header lost its title.** The banner guard exists to drop
  "DEPARTMENT OF ELECTRONICS AND COMMUNICATION ENGINEERING", and it was also
  dropping "BEC073 MICROWAVE AND RADAR ENGINEERING" — leaving a course with a
  code and no name. A line that opens with a course code is now a header
  whatever its case; a real banner has no code, and the three-digit tail keeps
  years out, so "AKTU 2026" is still not a course.
- **Titles carried the header furniture.** "Wireless and Mobile Communication
  3-0-0 3 Credits" was the course *name* — the L-T-P was extracted separately
  but the characters stayed in the title and travelled into the downloaded
  document. Credits, L-T-P in every spelling, flattened table columns and a
  leading `Course Title:` label are now stripped.
- **A recovered title landed on the wrong course.** The course boundary was
  drawn when a repeated `CO1` was spotted, which is *after* the next course's
  title line — so the name was filed under the previous course. A second
  "Course Outcomes:" heading now ends the previous course, which is what it
  actually means.
- **A headerless course had no name at all.** An elective printed as a banner
  and a bare title, with the code in a table the text extractor dropped, is now
  rescued by the last title-looking line above the heading.

That rescue is deliberately strict, because **a wrong course name is worse than
none**: a candidate must be 2–9 words, capitalised, and carry no terminal full
stop. "Microwave & Radar Engineering" is adopted; "Some intervening text about
references." is not, and that course stays blank rather than borrowing a name
it never had.

When nothing can be recovered the UI stops pretending. Instead of four
identical "Untitled" cards it shows `no header · block 3` with the opening of
that block's first outcome, and the count badge says how many courses came
through without a readable header.

**PDF extraction artifacts are repaired**, and only two of them: a hyphen with
a space on one side (`state -space`), and a word split mid-token
(`propulsi on`). The second needs a dictionary, so the document supplies its
own — fragments are joined only when the joined form appears elsewhere in the
same file, the fragment never appears standalone, and the joined form is no
rarer than the split. `based on` survives; `propulsi on` becomes
`propulsion`. The count of repairs is reported, never applied silently.

Confidence is reported honestly: `high` for structural DOCX tables, `medium`
or `low` for PDF text extraction. A numbered-list document declares no Bloom
levels at all, so it scores `low` and the levels are inferred from the verbs.

**An inferred K level says so.** This mattered more than it sounds: inference
fills `blooms_levels` in place, so `has_bloom` reported "declared" immediately
afterwards and every outcome in the UI wore a green *K declared* badge when the
scheme has no K column whatsoever. A guessed K feeds CL, and CL decides the
mapping level under Rule 2 — so the one thing faculty must be able to see is
which levels are the university's and which are ours. `ParsedCO.bloom_inferred`
now carries it through to the API and the badge.

The real scheme is pinned as a fixture rather than paraphrased:
`tests/fixtures/aktu_ece_vii_viii_2026-27.pdf` is the actual published PDF, and
`test_aktu_pdf_pipeline.py` drives it end to end — six courses stay six courses,
all 30 outcomes get a level, every level reports itself inferred, and no course
comes out with a wall of level-3 cells. Every parser bug found so far came from
something only the real file does.

### Rewriting what a real syllabus contains

Of the 30 outcomes in the AKTU ECE (Advanced Communication Technology) 2026-27
scheme, **13 fail the quality test** and all 13 are now repaired to pass it:

| Published | Rewritten |
| --- | --- |
| Designing of logic gates. | Design logic gates. |
| Implementation of combinational and sequential circuits using CMOS logic. | Implement combinational and sequential circuits using CMOS logic. |
| Express the basic knowledge of mobile radio fundamentals… | Describe mobile radio fundamentals… |
| Acquire knowledge about the handling and measurement of microwave equipment. | Describe handling and measurement of microwave equipment. |
| Recognize UAV control systems using modern control techniques… | Identify UAV control systems using modern control techniques… |
| Apply … basics **to understand** the concepts of equalization… | Apply … basics **to explain** the concepts of equalization… |

Three mechanisms, added because this document exposed the gap: nominalisations
(`Designing of X` → `Design X`), real verbs missing from the Bloom tables
(`Express`, `Acquire`, `Recognize`, `Do`), and vague verbs sitting *behind* a
good leading one.

The important part is the fallback. Where none of these apply the statement is
**left unchanged and flagged**, because the previous behaviour — prefixing a
verb regardless — produced `Apply Designing of logic gates.`, and a college
pastes what the tool proposes into a SAR unread. Emitting nothing is recoverable;
emitting mangled text is not.

## Question → CO tagging, and what it actually scores

Two layers, in this order:

1. **A rule-based scorer** — always on, no key, no network. Term overlap
   weighted by how few outcomes contain each term (so "serializability"
   decides a tag and "system" does not), plus acronym expansion (`ER` ↔
   entity-relationship) and Bloom-verb alignment.
2. **AI arbitration** — used *only* where layer 1 cannot separate two
   outcomes. Constrained to the course's own COs, temperature 0, forced JSON,
   model recorded on every result.

Set `ANTHROPIC_API_KEY` to enable layer 2. Without it, ambiguous questions are
flagged for review instead. A code the model invents is rejected, and any API
failure falls back to the deterministic answer.

**Measured, on 87 labelled questions across six courses** (`python -m
app.engine.tagger_eval`, floors enforced by `test_tagger_eval.py`):

| | all | held-out |
| --- | --- | --- |
| Accuracy | 72.4% | 78.7% |
| Coverage (answered without a human) | 70.1% | 74.5% |
| Precision on confident tags | 86.9% | 88.6% |
| **Silent error rate** | **9.2%** | **8.5%** |

The headline metric is the last one — questions tagged confidently and tagged
wrong. A flagged question costs a faculty member ten seconds; a confidently
mistagged one feeds the attainment arithmetic and moves a number nobody
re-checks. Coverage is reported alongside because the degenerate way to reach
zero silent errors is to flag everything.

### A negative result worth keeping

The corpus has a **dev/holdout split**, and it earned its keep immediately.
Sweeping the scorer's normalisation and verb weights against dev cut dev
silent errors from 15.0% to 5.0% — and made holdout *worse*, 8.5% → 12.8%.
The tuned values had fitted the vocabulary of two CS courses.

What survived was one change, not three: the confidence band had been
calibrated against the old score scale, so changing the scorer without
re-sweeping `HIGH_MARGIN` raised coverage while dropping precision — the
scorer got better while its self-assessment got worse. Reverting the weights
and keeping only that recalibration gave the numbers above, and
`test_both_splits_behave_similarly` now fails if the two splits ever diverge
that far again. With 87 questions, three weights is more than the data
supports, and `LENGTH_BIAS` is left parameterised to make the sweep
repeatable once the corpus grows.

The stemmer fixes were separate and kept: it now runs to a fixed point, since
one pass left `processes` → `process` but `process` → `proces`, so two forms
of the same word never matched.

## Migrating a 12-PO matrix

Eleven of the twelve columns are a rename. Old PO6 **and** old PO7 both fold
into new PO6 — two columns collapsing into one — and everything below shifts
up a slot. `engine/migration.py` handles the three consequences:

1. A collapsed cell takes the **higher** of the two levels (the outcome
   demonstrably reaches it on one of the merged concerns) and is flagged when
   they differed.
2. Column means move, so the migrated matrix is **re-validated**, not assumed.
3. Migration is not always right. The engine also **re-derives** the mapping
   from the CO text under v4 and lists the disagreements. Migration preserves
   the department's judgement and audit history; re-derivation may be better
   where the legacy matrix was already over-mapped. It recommends, and the
   department chooses.

Trend lines cross the same boundary: `engine/trends.py` aligns legacy years
through the crosswalk, averages the merged pair, and **marks every point that
came from a merge** so a chart cannot imply a comparison it has not earned.

## Why the automation is deterministic, not a model

Competency inference, the CO rewriter, PSO inference, and CQI drafting are all
rule-based. A deliberate constraint, not a limitation to fix later:

- **Reproducible.** The same CO text yields the same proposal in 2026 and in
  2029 during a re-accreditation audit. A model's output drifts when its
  version does.
- **Explainable.** Every proposal names the terms that fired, so the evidence
  bundle is a byproduct rather than an afterthought.
- **Offline and instant.** No service dependency in the critical path.

Embedding similarity would raise recall and is the natural next layer — but it
must sit *alongside* this audit trail, never replace it.

## Layout

```
backend/app/
  seed/gapc_v3_po.py     12 POs, legacy framework, verbatim
  seed/gapc_v4_po.py     11 POs + WK1-WK9 + crosswalk, verbatim
  seed/curriculum.py     starter course corpus, L-T-P -> NCrF conversion
  engine/frameworks.py   framework registry and PO-code migration
  engine/blooms.py       Bloom's levels -> action levels
  engine/co_quality.py   the 8-question CO quality test
  engine/co_rewriter.py  rewrites non-compliant CO statements
  engine/competency.py   infers CO x PO competency from text
  engine/pso.py          PSO quality checks and CO x PSO inference
  engine/mapping_rules.py  Rule 1 (competency) + Rule 2 (action level)
  engine/constraints.py  structural checks, course and program scope
  engine/ingest.py       marks parsing with column auto-detection
  engine/docx_parser.py  .docx question papers, .xlsx marks sheets
  engine/syllabus_parser.py  reads CO/KL tables and unit-wise content from PDF/DOCX/text
  engine/qco_tagger.py   question -> CO, rules first then optional AI
  engine/tagger_eval.py  labelled-corpus metrics for the tagger
  engine/copo_eval.py    labelled-corpus metrics for CO x PO competency
  engine/copo_document.py  PO averages + the downloadable articulation matrix
  engine/verb_advisor.py   vague or missing verb -> measurable verb, with the level cost
  engine/conventions.py    departmental conventions, deliberately off the inference path
  engine/attainment.py   CO and PO attainment, both target modes, choice policy
  engine/indirect.py     surveys, scoring, direct+indirect blending
  engine/trends.py       multi-year series, gaps, CQI closure
  engine/migration.py    12 -> 11 matrix migration and comparison
  engine/cqi.py          Criterion 8 improvement drafts
  engine/sar_export.py   SAR Tables 1.4.2.1, 1.5.1, 3.8.1
  engine/office_export.py  .docx / .xlsx renderers for the annexures
  store/db.py            SQLite persistence, tenant-bound access
  store/postgres_schema.sql  deployment schema with real RLS policies
  store/audit.py         hash-chained log and evidence bundles
  store/workflow.py      roles and the approval chain
  api/main.py            FastAPI layer + single-page UI
  api/workspace.py       persistence, audit, PSO, survey, trend, export routes
tests/eval/qco_corpus.json    87 labelled questions, dev/holdout split
tests/eval/copo_corpus.json   48 labelled outcomes with gold 0/1/2 competency
tests/fixtures/aktu_ece_vii_viii_2026-27.pdf   the real published scheme
static/index.html             the CO -> PO screen
static/workspace-full.html    the full workspace, parked but live
```

## The dual-framework rule

GAPC v3 (12 POs) and GAPC v4.0 (11 POs) are both first-class and permanent. A
college has graduated cohorts mapped under v3 and current cohorts under v4,
often in the same year, and Criterion 8 needs trend lines across that boundary.

- Old PO6 **and** old PO7 both map to new PO6 — a merge, not a rename.
- Old PO8–PO12 shift down to new PO7–PO11.

PO ordinals are therefore **not** stable across versions. Everything resolves
through `(framework_version, po_code)`; nothing hardcodes a PO count. Switch
the framework dropdown and the matrix grows a twelfth column with no code
change. `test_frameworks.py` enforces this, and the API rejects `PO12` under
v4 with a message that says why.

## Design rules

1. **No AI in the attainment path.** AI may suggest mappings and draft
   narrative; it never produces a number that reaches a SAR.
2. **Frameworks are immutable.** If NBA revises wording, add a version — never
   mutate one that has live data against it.
3. **Faculty own competency judgements.** The engine proposes and derives
   levels; a human decides, and overrides are visually distinct from proposals.
4. **`-` is not `0`.** An unaddressed PO is distinct from one attained at zero.
   Orphan POs are an automatic NBA finding, so they stay visible.
5. **The browser renders; it does not calculate.** No arithmetic is duplicated
   in JavaScript, so there is one source of truth for any number.
6. **Absent is not zero, and not-attempted is neither.** An absentee is
   excluded; a student who skipped an optional question is counted according
   to a declared policy.
7. **Never emit text that is worse than silence.** A rewrite that cannot be
   made grammatical is flagged, not guessed at.
8. **Units are named, never inferred.** Percentages and attainment levels are
   both plain floats, so the scale is passed explicitly and mismatches raise.
9. **Tenant isolation is structural.** No store method accepts a tenant id, so
   no call site can address another college's data.

## Source provenance

| Data | Source | Confidence |
| --- | --- | --- |
| 11 POs, WK1–WK9, attainment method, table shapes | Official NBA SAR (GAPC v4.0, Jan 2025), nbaind.org, read in full | Verbatim |
| 12 POs (legacy) | Published NBA-accredited institution's PO document | Cross-checked — **re-verify against the archived NBA circular before customer-facing use** |
| CO quality tests, Rule 1 / Rule 2 mapping | *Basic guidelines for framing COs* (client-supplied) | As specified |
| PO `default_action_level`, PSO action levels | Editorial default from each statement's dominant verb | **Not NBA-prescribed** — review per institution |
| Competency signal lexicon | Derived from the verbatim PO and WK statements | Tuned to under-propose; **measured** at 6.2% false-strong on held-out courses |
| Specialization vocabulary | PO1's WK4, "engineering specialist knowledge" | Cannot reach a strong match; ECE/CS coverage only |
| PO average denominators | AKTU articulation-matrix convention | Both computed; **pick one and state it in the methodology note** |
| CO → PO gold labels | Our reading of each outcome against the verbatim POs | **Our judgement, not published matrices** — those over-map |
| 80:20 direct:indirect weighting | Common Indian practice | **Convention, not in the SAR** — declare it in the methodology note |
| 25% minimum response rate | Institutional convention | Not an NBA rule; the point below which self-selection dominates |
| NCrF hour conversion | 30 hours = 1 credit (SAR 1.2.1), 15-week semester assumed | The week count is an institutional convention — state it |

The action levels driving Rule 2 are our defaults, not NBA's; the competency
lexicon is calibrated to miss rather than over-claim, because a missed mapping
is a cheap human fix while a spurious weight-3 that gets bulk-approved is the
failure mode the plan flags as high-severity.

## Known limits

- **CO → PO strong recall is 60% on held-out courses.** Two in five genuinely
  strong mappings are proposed at 1 instead of 2, so a faculty member still
  reads every row and upgrades some. The matrix is a reviewed draft, not an
  answer. This is the deliberate side of the trade — see the measurement table
  above — but it is the number to quote, not the 88% cell accuracy.
- **48 labelled outcomes is a small corpus.** Every figure above carries wide
  error bars, and the dev/holdout gap in strong recall (83% vs 60%) shows the
  lexicon still has course-specific fit. Treat the floors in `test_copo_eval.py`
  as ratchets, not as a claim about a new department's courses.
- **The specialization vocabulary is ECE- and CS-leaning.** It is what raised
  held-out recall to 90.5%, and it covers the disciplines the corpus contains.
  Another branch — civil, mechanical, chemical — will hit the same silent zeros
  until its own vocabulary is added, and the symptom is a PO1 column that is
  emptier than it should be.
- **The held-out split has been looked at.** The PO6/PO7/PO11 vocabularies were
  written from the verbatim PO and WK text rather than from the misses, but the
  misses were read while diagnosing, so the held-out figures are no longer a
  clean generalisation estimate. They need fresh courses to be one again.
- **No authentication.** The actor and role arrive in headers and are trusted.
  Fine behind an institutional login for a single college; **not adequate for
  the hosted multi-tenant product** — anyone who can reach the API can claim
  to be the coordinator. The approval chain is an accountability record, not
  an access control, until real auth sits in front of it.
- **The Postgres path is reviewed, not exercised.** `postgres_schema.sql`
  carries real `CREATE POLICY` statements and a verification procedure, but
  the test suite runs against SQLite. Run the checks in that file before
  trusting it with student marks.
- **The live Anthropic call is untested.** No `ANTHROPIC_API_KEY` was
  available here. The arbitration logic — JSON parsing, hallucinated-code
  rejection, every failure path — is covered by a stub client; the network
  call itself is not.
- **Scanned syllabus PDFs need OCR** and are not supported. The parser says so
  rather than returning empty outcomes.
- **Student marks are personal data** under the DPDP Act 2023. Tenant
  isolation and pseudonymous survey responses are built in; encryption at
  rest, retention policy, and consent records are not.
