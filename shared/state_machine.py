from __future__ import annotations

from collections.abc import Mapping

from shared.enums import JobState, RoleRunState, SessionState


class StateTransitionError(ValueError):
    """Raised when a state transition is invalid."""


def _assert_transition(
    current: str,
    target: str,
    allowed_transitions: Mapping[str, set[str]],
    subject: str,
) -> str:
    allowed = allowed_transitions.get(current, set())
    if target not in allowed and target != current:
        raise StateTransitionError(
            f"{subject} cannot transition from {current!r} to {target!r}; allowed={sorted(allowed)!r}"
        )
    return target


SESSION_TRANSITIONS: dict[str, set[str]] = {
    SessionState.DRAFT: {SessionState.SELECTING_ARTIFACTS, SessionState.CANCELLED},
    SessionState.SELECTING_ARTIFACTS: {
        SessionState.AWAITING_HOSTS,
        SessionState.BUILDING_ARTIFACTS,
        SessionState.CANCELLED,
        SessionState.FAILED,
    },
    SessionState.BUILDING_ARTIFACTS: {
        SessionState.SELECTING_ARTIFACTS,
        SessionState.AWAITING_HOSTS,
        SessionState.FAILED,
        SessionState.CANCELLED,
    },
    SessionState.AWAITING_HOSTS: {
        SessionState.SELECTING_ARTIFACTS,
        SessionState.DISTRIBUTING_ARTIFACTS,
        SessionState.FAILED,
        SessionState.CANCELLED,
    },
    SessionState.DISTRIBUTING_ARTIFACTS: {
        SessionState.PREPARING_ROLES,
        SessionState.FAILED,
        SessionState.CANCELLED,
    },
    SessionState.PREPARING_ROLES: {
        SessionState.READY_TO_CAPTURE,
        SessionState.FAILED,
        SessionState.CANCELLED,
    },
    SessionState.READY_TO_CAPTURE: {
        SessionState.CAPTURING,
        SessionState.FAILED,
        SessionState.CANCELLED,
    },
    SessionState.CAPTURING: {
        SessionState.MERGING,
        SessionState.FAILED,
        SessionState.CANCELLED,
    },
    SessionState.MERGING: {
        SessionState.REPORT_READY,
        SessionState.FAILED,
    },
    SessionState.REPORT_READY: set(),
    SessionState.FAILED: set(),
    SessionState.CANCELLED: set(),
}


ROLE_RUN_TRANSITIONS: dict[str, set[str]] = {
    RoleRunState.IDLE: {RoleRunState.ASSIGNED, RoleRunState.FAILED},
    RoleRunState.ASSIGNED: {RoleRunState.ARTIFACT_PENDING, RoleRunState.FAILED},
    RoleRunState.ARTIFACT_PENDING: {RoleRunState.ARTIFACT_READY, RoleRunState.FAILED},
    RoleRunState.ARTIFACT_READY: {RoleRunState.FLASHING, RoleRunState.FAILED},
    RoleRunState.FLASHING: {RoleRunState.FLASH_VERIFIED, RoleRunState.FAILED},
    RoleRunState.FLASH_VERIFIED: {RoleRunState.PREPARE_CAPTURE, RoleRunState.FAILED},
    RoleRunState.PREPARE_CAPTURE: {RoleRunState.CAPTURE_READY, RoleRunState.FAILED},
    RoleRunState.CAPTURE_READY: {RoleRunState.CAPTURING, RoleRunState.FAILED},
    RoleRunState.CAPTURING: {RoleRunState.COMPLETED, RoleRunState.FAILED},
    RoleRunState.COMPLETED: set(),
    RoleRunState.FAILED: set(),
}


JOB_TRANSITIONS: dict[str, set[str]] = {
    JobState.PENDING: {JobState.RUNNING, JobState.CANCELLED, JobState.FAILED},
    JobState.RUNNING: {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED},
    JobState.COMPLETED: set(),
    JobState.FAILED: set(),
    JobState.CANCELLED: set(),
}


def transition_session(current: SessionState, target: SessionState) -> SessionState:
    return SessionState(
        _assert_transition(current, target, SESSION_TRANSITIONS, subject="session")
    )


def transition_role_run(current: RoleRunState, target: RoleRunState) -> RoleRunState:
    return RoleRunState(
        _assert_transition(current, target, ROLE_RUN_TRANSITIONS, subject="role run")
    )


def transition_job(current: JobState, target: JobState) -> JobState:
    return JobState(_assert_transition(current, target, JOB_TRANSITIONS, subject="job"))

