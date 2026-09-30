"""
Roles and the approval chain.

An NBA submission is signed by people, and the peer team asks who signed it.
So the states here are not workflow decoration -- they are the record of who
stood behind a number.

    draft  --submit-->  submitted  --approve-->  approved  --lock-->  locked
                            |                        |
                            +------- return ---------+
                                       |
                                       v
                                    returned  --submit-->  submitted

`locked` is terminal by design: the point of a lock is that the SAR figure
cannot move under the peer team after it was shown to them. Unlocking is a
separate, audited, coordinator-only act rather than a state transition, so it
can never happen as a side effect of an ordinary edit.

Who may do what follows the actual hierarchy in an Indian engineering college:
faculty own their course, the HOD owns the department's submission, and the
NBA coordinator owns the SAR.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.store.db import TenantStore, new_id, _now

ROLES: frozenset[str] = frozenset({"faculty", "hod", "coordinator", "admin"})

STATES: tuple[str, ...] = ("draft", "submitted", "approved", "locked", "returned")

# state -> action -> (next_state, roles allowed)
_TRANSITIONS: dict[str, dict[str, tuple[str, frozenset[str]]]] = {
    "draft": {
        "submit": ("submitted", frozenset({"faculty", "hod", "coordinator", "admin"})),
    },
    "returned": {
        "submit": ("submitted", frozenset({"faculty", "hod", "coordinator", "admin"})),
    },
    "submitted": {
        "approve": ("approved", frozenset({"hod", "coordinator", "admin"})),
        "return": ("returned", frozenset({"hod", "coordinator", "admin"})),
    },
    "approved": {
        "lock": ("locked", frozenset({"coordinator", "admin"})),
        "return": ("returned", frozenset({"hod", "coordinator", "admin"})),
    },
    "locked": {},          # terminal; see unlock() below
}


class TransitionDenied(RuntimeError):
    """The transition is not legal from this state, or not for this role."""


@dataclass(frozen=True)
class ApprovalState:
    entity_type: str
    entity_id: str
    state: str
    actor: str
    note: str
    at: str

    @property
    def is_locked(self) -> bool:
        return self.state == "locked"

    @property
    def is_editable(self) -> bool:
        """Only a locked entity is closed to edits; everything else is open."""
        return self.state != "locked"


def current_state(store: TenantStore, entity_type: str, entity_id: str) -> ApprovalState:
    row = store._one(
        "SELECT * FROM approvals WHERE tenant_id = ? AND entity_type = ? AND entity_id = ? "
        "ORDER BY at DESC, rowid DESC LIMIT 1",
        (store.tenant_id, entity_type, entity_id),
    )
    if row is None:
        return ApprovalState(entity_type, entity_id, "draft", "", "", "")
    return ApprovalState(entity_type, entity_id, row["state"], row["actor"],
                         row["note"], row["at"])


def history(store: TenantStore, entity_type: str, entity_id: str) -> list[dict]:
    return store._rows(
        "SELECT * FROM approvals WHERE tenant_id = ? AND entity_type = ? AND entity_id = ? "
        "ORDER BY at, rowid",
        (store.tenant_id, entity_type, entity_id),
    )


def available_actions(state: str, role: str) -> list[str]:
    return sorted(
        action for action, (_, roles) in _TRANSITIONS.get(state, {}).items()
        if role in roles
    )


def transition(store: TenantStore, *, entity_type: str, entity_id: str,
               action: str, actor: str, role: str, note: str = "") -> ApprovalState:
    """
    Apply an approval action. Raises TransitionDenied rather than silently
    no-oping, because a submission that quietly did not happen is worse than
    an error message.
    """
    if role not in ROLES:
        raise TransitionDenied(f"Unknown role {role!r}.")
    state = current_state(store, entity_type, entity_id).state
    allowed = _TRANSITIONS.get(state, {})
    if action not in allowed:
        if state == "locked":
            raise TransitionDenied(
                f"{entity_type} {entity_id} is locked. Unlocking is a separate "
                f"coordinator action, so that a SAR figure cannot move as a side "
                f"effect of an ordinary edit."
            )
        raise TransitionDenied(
            f"Cannot '{action}' from state '{state}'. Available here: "
            f"{sorted(allowed) or 'nothing'}."
        )
    next_state, roles = allowed[action]
    if role not in roles:
        raise TransitionDenied(
            f"Role '{role}' cannot '{action}' a {entity_type}. "
            f"Allowed: {sorted(roles)}."
        )
    _record(store, entity_type, entity_id, next_state, actor, note)
    return ApprovalState(entity_type, entity_id, next_state, actor, note, _now())


def unlock(store: TenantStore, *, entity_type: str, entity_id: str, actor: str,
           role: str, reason: str) -> ApprovalState:
    """
    Deliberately not a transition. A lock exists so a number cannot move
    underneath a peer team; breaking one is an explicit, reasoned act that
    only a coordinator can take, and the reason is mandatory because that is
    the sentence the peer team will actually read.
    """
    if role not in ("coordinator", "admin"):
        raise TransitionDenied(f"Role '{role}' cannot unlock. Coordinator or admin only.")
    if not reason.strip():
        raise TransitionDenied("Unlocking requires a stated reason.")
    state = current_state(store, entity_type, entity_id).state
    if state != "locked":
        raise TransitionDenied(f"{entity_type} {entity_id} is not locked (state: {state}).")
    _record(store, entity_type, entity_id, "returned", actor, f"UNLOCKED: {reason}")
    return ApprovalState(entity_type, entity_id, "returned", actor, reason, _now())


def require_editable(store: TenantStore, entity_type: str, entity_id: str) -> None:
    st = current_state(store, entity_type, entity_id)
    if not st.is_editable:
        raise TransitionDenied(
            f"{entity_type} {entity_id} is locked and cannot be edited."
        )


def _record(store: TenantStore, entity_type: str, entity_id: str, state: str,
            actor: str, note: str) -> None:
    store._write(
        "INSERT INTO approvals (id, tenant_id, entity_type, entity_id, state, actor, "
        "note, at) VALUES (?,?,?,?,?,?,?,?)",
        (new_id(), store.tenant_id, entity_type, entity_id, state, actor, note, _now()),
    )
