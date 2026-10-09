"""Mission lifecycle state machine.

    DRAFT -> PLANNING -> VALIDATING -> ARMED -> ACTIVE -> COMPLETED
                 \\            \\          \\        \\
                  +------------+----------+--------+--> ABORTED

Additional, deliberately narrow edges:
    VALIDATING -> PLANNING   operator went back to edit the plan
    ARMED      -> VALIDATING disarm (e.g. to change drones) before launch
    DRAFT      -> ABORTED    discard a mission that was never planned

Everything else (DRAFT -> ACTIVE, ACTIVE -> DRAFT, COMPLETED -> ACTIVE,
ABORTED -> anything, ...) is rejected. Guards run server-side on every
transition, so a client cannot skip validation by calling the API directly.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.logging import log_event
from app.models.mission import Mission
from app.models.mission_planning import MissionStateHistory
from app.models.user import User
from app.repositories.audit_repository import AuditRepository

logger = logging.getLogger("dronematrx.mission.state")

TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"planning", "aborted"}),
    "planning": frozenset({"validating", "aborted"}),
    "validating": frozenset({"armed", "planning", "aborted"}),
    "armed": frozenset({"active", "validating", "aborted"}),
    "active": frozenset({"completed", "aborted"}),
    "completed": frozenset(),
    "aborted": frozenset(),
}
TERMINAL_STATES = frozenset(state for state, targets in TRANSITIONS.items() if not targets)
EDITABLE_STATES = frozenset({"draft", "planning", "validating"})


class InvalidTransitionError(Exception):
    def __init__(self, current: str, target: str):
        self.current, self.target = current, target
        allowed = ", ".join(sorted(TRANSITIONS.get(current, ()))) or "none (terminal state)"
        super().__init__(f"Cannot transition mission from {current.upper()} to {target.upper()}. Allowed: {allowed}")


class TransitionBlockedError(Exception):
    """A legal transition whose safety/readiness guard failed."""

    def __init__(self, message: str, checks: list[dict] | None = None, report: dict | None = None):
        super().__init__(message)
        self.checks = checks or []
        self.report = report


def is_valid_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


class MissionStateService:
    def __init__(self, db: Session):
        self.db = db

    def transition(
        self,
        mission: Mission,
        target: str,
        user: User | None,
        reason: str | None = None,
        ip_address: str | None = None,
        *,
        run_guards: bool = True,
    ) -> Mission:
        current = mission.status
        if not is_valid_transition(current, target):
            log_event(logger, "mission.transition_rejected", logging.WARNING, mission_id=mission.id, current=current, target=target)
            raise InvalidTransitionError(current, target)

        if run_guards:
            self._guard(mission, target, user)

        now = datetime.now(timezone.utc)
        mission.status = target
        if target == "active":
            mission.start_time = now
        elif target in ("completed", "aborted"):
            mission.end_time = now
        if target == "planning":
            mission.validation_report = None
            mission.validated_at = None

        self.db.add(
            MissionStateHistory(
                mission_id=mission.id,
                previous_state=current,
                new_state=target,
                user_id=user.id if user else None,
                reason=reason,
                # Explicit clock time: now() is constant inside one DB transaction.
                created_at=now.replace(tzinfo=None),
            )
        )
        # AuditRepository.record commits the transition and its history row together.
        AuditRepository(self.db).record(
            user_id=user.id if user else None,
            action="mission_state_changed",
            resource="mission",
            resource_id=str(mission.id),
            ip_address=ip_address,
            metadata={"from": current, "to": target, "reason": reason},
        )
        self.db.refresh(mission)
        log_event(logger, "mission.state_changed", mission_id=mission.id, previous=current, new=target, user_id=user.id if user else None)
        return mission

    def _guard(self, mission: Mission, target: str, user: User | None) -> None:
        from app.services.mission_planning_service import MissionPlanningService

        planning = MissionPlanningService(self.db)
        if target == "validating":
            missing = planning.missing_steps(mission)
            if missing:
                raise TransitionBlockedError(f"Complete wizard steps {missing} before validation")
        elif target == "armed":
            # Never trust a cached report: re-run every check (incl. PostGIS NFZ) now.
            report = planning.validate(mission, user, transition=False)
            if not report["passed"]:
                errors = [c for c in report["checks"] if c["severity"] == "error"]
                message = "NFZ conflict blocks arming" if report["nfz_conflict"] else "Mission failed pre-arm validation"
                raise TransitionBlockedError(message, errors, report)
        elif target == "active":
            checks = planning.readiness_checks(mission)
            errors = [c for c in checks if c["severity"] == "error"]
            if errors:
                raise TransitionBlockedError("Mission is not ready to launch", errors)
