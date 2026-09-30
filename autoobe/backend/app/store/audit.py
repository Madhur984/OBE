
"""
Tamper-evident audit log, and the evidence bundle built from it.

The question this module exists to answer is the one a peer team actually
asks: *"how did PO3 become 2.14?"* Before this, the honest answer was "re-run
it and see" -- which is not evidence, because it proves what the software
does today, not what it did when the SAR was signed.

Two mechanisms:

**Hash chaining.** Each event carries the hash of the one before it, so an
altered or deleted row breaks the chain at that point and `verify_chain`
names it. This does not make the log unforgeable -- anyone who can write the
database can rewrite the whole chain -- and it is not claimed to. What it
does is make silent, partial edits detectable, which is the realistic
scenario: a row quietly adjusted the week before a visit.

**Evidence bundles.** A number plus every input that produced it: the COs,
the mapping levels and whether each was proposed or overridden, the
questions, the marks, the thresholds, the target mode, and the approval
history. Assembled from stored data, not recomputed, so a bundle from 2026
still reads as it did in 2026 even after the engine changes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app.store.db import TenantStore, new_id, _now

GENESIS = "0" * 64


def _digest(*, tenant_id: str, seq: int, at: str, actor: str, action: str,
            entity_type: str, entity_id: str, payload: str, prev_hash: str) -> str:
    # sort_keys is applied by the caller when serialising payload, so the same
    # logical event always digests identically.
    blob = "\x1f".join([tenant_id, str(seq), at, actor, action, entity_type,
                        entity_id, payload, prev_hash])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    at: str
    actor: str
    action: str
    entity_type: str
    entity_id: str
    payload: dict
    prev_hash: str
    hash: str


def record(store: TenantStore, *, actor: str, action: str, entity_type: str,
           entity_id: str, payload: dict[str, Any] | None = None) -> AuditEvent:
    """Append one event. Never updates or deletes -- that is the whole point."""
    last = store._one(
        "SELECT seq, hash FROM audit_events WHERE tenant_id = ? "
        "ORDER BY seq DESC LIMIT 1",
        (store.tenant_id,),
    )
    seq = (last["seq"] + 1) if last else 1
    prev_hash = last["hash"] if last else GENESIS
    at = _now()
    blob = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)
    h = _digest(tenant_id=store.tenant_id, seq=seq, at=at, actor=actor, action=action,
                entity_type=entity_type, entity_id=entity_id, payload=blob,
                prev_hash=prev_hash)
    store._write(
        "INSERT INTO audit_events (id, tenant_id, seq, at, actor, action, entity_type, "
        "entity_id, payload, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (new_id(), store.tenant_id, seq, at, actor, action, entity_type, entity_id,
         blob, prev_hash, h),
    )
    return AuditEvent(seq, at, actor, action, entity_type, entity_id,
                      payload or {}, prev_hash, h)


def events(store: TenantStore, *, entity_type: str | None = None,
           entity_id: str | None = None, limit: int | None = None) -> list[AuditEvent]:
    sql = "SELECT * FROM audit_events WHERE tenant_id = ?"
    params: list[Any] = [store.tenant_id]
    if entity_type:
        sql += " AND entity_type = ?"
        params.append(entity_type)
    if entity_id:
        sql += " AND entity_id = ?"
        params.append(entity_id)
    sql += " ORDER BY seq"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [
        AuditEvent(r["seq"], r["at"], r["actor"], r["action"], r["entity_type"],
                   r["entity_id"], json.loads(r["payload"]), r["prev_hash"], r["hash"])
        for r in store._rows(sql, params)
    ]


@dataclass(frozen=True)
class ChainCheck:
    ok: bool
    checked: int
    problems: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.ok:
            return f"{self.checked} events verified; chain intact."
        return f"{self.checked} events checked; {len(self.problems)} problem(s)."


def verify_chain(store: TenantStore) -> ChainCheck:
    """
    Recompute every hash and confirm each event points at its predecessor.

    Reports the *first* broken link prominently, because once the chain
    breaks every later event mismatches too and a wall of errors hides where
    the tampering actually happened.
    """
    rows = store._rows(
        "SELECT * FROM audit_events WHERE tenant_id = ? ORDER BY seq", (store.tenant_id,)
    )
    problems: list[str] = []
    expected_prev = GENESIS
    expected_seq = 1
    for r in rows:
        if r["seq"] != expected_seq:
            problems.append(
                f"Sequence jumps from {expected_seq - 1} to {r['seq']} — "
                f"{r['seq'] - expected_seq} event(s) deleted."
            )
            expected_seq = r["seq"]
        if r["prev_hash"] != expected_prev:
            problems.append(f"Event {r['seq']} does not follow event {r['seq'] - 1}.")
        recomputed = _digest(
            tenant_id=store.tenant_id, seq=r["seq"], at=r["at"], actor=r["actor"],
            action=r["action"], entity_type=r["entity_type"], entity_id=r["entity_id"],
            payload=r["payload"], prev_hash=r["prev_hash"],
        )
        if recomputed != r["hash"]:
            problems.append(
                f"Event {r['seq']} ({r['action']} on {r['entity_type']} "
                f"{r['entity_id']}) was altered after it was written."
            )
        expected_prev = r["hash"]
        expected_seq += 1
    return ChainCheck(not problems, len(rows), problems)


# ---------------------------------------------------------------------------
# Evidence bundles
# ---------------------------------------------------------------------------

@dataclass
class EvidenceBundle:
    """Everything behind one course's attainment figures, as stored."""
    course: dict
    academic_year: str
    outcomes: list[dict]
    mapping: list[dict]
    assessments: list[dict]
    thresholds: dict
    result: dict
    approvals: list[dict]
    audit: list[dict]
    chain: dict

    def to_dict(self) -> dict:
        return {
            "course": self.course,
            "academic_year": self.academic_year,
            "outcomes": self.outcomes,
            "mapping": self.mapping,
            "assessments": self.assessments,
            "thresholds": self.thresholds,
            "result": self.result,
            "approvals": self.approvals,
            "audit": self.audit,
            "chain": self.chain,
        }

    def to_markdown(self) -> str:
        c = self.course
        lines = [
            f"# Evidence bundle — {c.get('code', '?')} {c.get('title', '')}",
            "",
            f"Academic year: **{self.academic_year}**",
            f"Audit chain: **{self.chain.get('summary', 'not checked')}**",
            "",
            "## Course outcomes",
            "",
            "| CO | Statement | Bloom | Source |",
            "| --- | --- | --- | --- |",
        ]
        for co in self.outcomes:
            src = "rewritten" if co.get("original_statement") else "as published"
            bl = ",".join(f"K{b}" for b in co.get("blooms_levels", [])) or "—"
            lines.append(f"| {co['code']} | {co['statement']} | {bl} | {src} |")

        lines += ["", "## Mapping, with provenance", "",
                  "| CO | Target | Level | Competency | Source | Evidence |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for m in self.mapping:
            lines.append(
                f"| {m['co_code']} | {m['target_code']} | "
                f"{m['level'] if m['level'] else '-'} | {m['competency']} | "
                f"{m['source']} | {m.get('evidence', '')} |"
            )

        lines += ["", "## Assessment evidence", ""]
        for a in self.assessments:
            lines.append(f"**{a['name']}** ({a['kind']})")
            lines.append("")
            lines.append("| Q | CO | Max | Present | Met target | % |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for q in a.get("questions", []):
                lines.append(
                    f"| {q['question_no']} | {q['co_code']} | {q['max_marks']} | "
                    f"{q.get('present', '')} | {q.get('met', '')} | "
                    f"{q.get('passed_pct', '')} |"
                )
            lines.append("")

        lines += ["## Thresholds and policy", "",
                  "```json", json.dumps(self.thresholds, indent=2), "```", ""]

        if self.approvals:
            lines += ["## Approval history", "", "| When | State | By | Note |",
                      "| --- | --- | --- | --- |"]
            for a in self.approvals:
                lines.append(f"| {a['at']} | {a['state']} | {a['actor']} | {a.get('note','')} |")
            lines.append("")

        lines += ["## Change log", "", "| # | When | Who | Action | Entity |",
                  "| --- | --- | --- | --- | --- |"]
        for e in self.audit:
            lines.append(
                f"| {e['seq']} | {e['at']} | {e['actor']} | {e['action']} | "
                f"{e['entity_type']}:{e['entity_id'][:8]} |"
            )
        return "\n".join(lines)


def build_evidence_bundle(store: TenantStore, *, course_id: str,
                          academic_year: str, run_id: str | None = None,
                          entity_key: str | None = None) -> EvidenceBundle:
    """
    Assemble the bundle from stored rows. Nothing here recomputes attainment:
    the stored run *is* the record, so a bundle reproduced years later shows
    what was submitted, not what today's engine would produce.

    `entity_key` is what approvals and audit events were filed under. It
    defaults to the course *code*, not its row id: the code is stable and
    exists before anything is stored, so the sign-off panel can address a
    course on first load. Events filed under the row id are collected too,
    because getting this wrong silently produces an empty change log — which
    reads as "nothing happened" rather than "we looked in the wrong place".
    """
    from app.store import workflow

    course = store.get_course(course_id) or {}
    key = entity_key or course.get("code") or course_id
    outcomes = store.list_cos(course_id)
    mapping = store.list_mapping(course_id)

    assessments: list[dict] = []
    for a in store.list_assessments(course_id, academic_year):
        qs = []
        for q in store.list_questions(a["id"]):
            marks = store.list_marks(q["id"])
            present = [m for m in marks if m["status"] == "present"]
            qs.append({
                "question_no": q["question_no"],
                "co_code": q["co_code"],
                "max_marks": q["max_marks"],
                "choice_group": q["choice_group"],
                "present": len(present),
                "absent": sum(1 for m in marks if m["status"] == "absent"),
                "not_attempted": sum(1 for m in marks if m["status"] == "not_attempted"),
            })
        assessments.append({**a, "questions": qs})

    if run_id:
        run = store.get_run(run_id)
    else:
        runs = [r for r in store.list_runs(course_id=course_id)
                if r["academic_year"] == academic_year]
        run = runs[-1] if runs else None

    # Merge in the computed per-question outcomes from the run, so the bundle
    # shows the target each question was judged against, not just its marks.
    if run:
        by_q = {}
        for co in (run["result"].get("co_attainment") or {}).values():
            for q in (co.get("questions") or []):
                by_q[q.get("question_no")] = q
        for a in assessments:
            for q in a["questions"]:
                extra = by_q.get(q["question_no"])
                if extra:
                    q["met"] = extra.get("students_meeting_target")
                    q["passed_pct"] = round(extra.get("passed_pct", 0.0), 1)
                    q["target_marks"] = extra.get("target_marks")

    seen: dict[int, dict] = {}
    for candidate in dict.fromkeys([key, course_id]):
        for e in events(store, entity_id=candidate):
            seen[e.seq] = {"seq": e.seq, "at": e.at, "actor": e.actor,
                           "action": e.action, "entity_type": e.entity_type,
                           "entity_id": e.entity_id}

    approvals = workflow.history(store, "course_matrix", key)
    if not approvals and key != course_id:
        approvals = workflow.history(store, "course_matrix", course_id)

    chain = verify_chain(store)
    return EvidenceBundle(
        course=course,
        academic_year=academic_year,
        outcomes=outcomes,
        mapping=mapping,
        assessments=assessments,
        thresholds=(run or {}).get("config", {}),
        result=(run or {}).get("result", {}),
        approvals=approvals,
        audit=[seen[k] for k in sorted(seen)],
        chain={"ok": chain.ok, "summary": chain.summary, "problems": chain.problems},
    )
