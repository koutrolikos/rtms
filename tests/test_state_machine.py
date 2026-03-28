import pytest

from rtms.shared.enums import JobState, RoleRunState, SessionState
from rtms.shared.state_machine import StateTransitionError, transition_job, transition_role_run, transition_session


def test_session_happy_path_transitions() -> None:
    state = transition_session(SessionState.DRAFT, SessionState.SELECTING_ARTIFACTS)
    state = transition_session(state, SessionState.BUILDING_ARTIFACTS)
    state = transition_session(state, SessionState.AWAITING_HOSTS)
    state = transition_session(state, SessionState.DISTRIBUTING_ARTIFACTS)
    state = transition_session(state, SessionState.PREPARING_ROLES)
    state = transition_session(state, SessionState.READY_TO_CAPTURE)
    state = transition_session(state, SessionState.CAPTURING)
    state = transition_session(state, SessionState.MERGING)
    state = transition_session(state, SessionState.REPORT_READY)
    assert state is SessionState.REPORT_READY


def test_invalid_role_run_transition_raises() -> None:
    with pytest.raises(StateTransitionError):
        transition_role_run(RoleRunState.IDLE, RoleRunState.CAPTURING)


def test_job_cancel_from_pending() -> None:
    assert transition_job(JobState.PENDING, JobState.CANCELLED) is JobState.CANCELLED

