"""Tests for the shared file loader (ui.sidebar).

The behaviour under test is subtle and caused a real regression: Streamlit's
``file_uploader`` returns its file on *every* rerun, not only the run where it was
chosen. Treating that as "a file was just uploaded" re-read the file and cleared
every result on each interaction — so running the optimizer and then pressing
anything discarded the plan, and the Comparison tab insisted the optimizer had not
been run.
"""

from __future__ import annotations

import io

import pytest
import streamlit as st

from ui import sidebar
from ui import state as S


class FakeUpload:
    """Stands in for Streamlit's UploadedFile."""

    def __init__(self, name="sites.xlsx", payload=b"DATA", file_id=None):
        self.name = name
        self.size = len(payload)
        self._payload = payload
        if file_id is not None:
            self.file_id = file_id

    def read(self):
        return self._payload


@pytest.fixture(autouse=True)
def clean_session():
    st.session_state.clear()
    S.init_state(st.session_state)
    yield
    st.session_state.clear()


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def test_first_upload_is_read():
    upload = FakeUpload()
    assert sidebar._newly_uploaded(upload, S.SITES_FP) == b"DATA"


def test_the_same_file_on_a_later_rerun_is_ignored():
    """The regression, in one assertion."""
    upload = FakeUpload()
    assert sidebar._newly_uploaded(upload, S.SITES_FP) == b"DATA"
    # Streamlit hands back the same file on every rerun.
    assert sidebar._newly_uploaded(upload, S.SITES_FP) is None
    assert sidebar._newly_uploaded(upload, S.SITES_FP) is None


def test_a_different_file_is_read_again():
    sidebar._newly_uploaded(FakeUpload(name="a.xlsx", payload=b"AAA"), S.SITES_FP)
    second = sidebar._newly_uploaded(
        FakeUpload(name="b.xlsx", payload=b"BBBB"), S.SITES_FP
    )
    assert second == b"BBBB"


def test_same_name_but_different_size_counts_as_a_change():
    sidebar._newly_uploaded(FakeUpload(name="s.xlsx", payload=b"AA"), S.SITES_FP)
    again = sidebar._newly_uploaded(
        FakeUpload(name="s.xlsx", payload=b"AAAA"), S.SITES_FP
    )
    assert again == b"AAAA"


def test_streamlit_file_id_is_preferred_when_present():
    first = FakeUpload(name="s.xlsx", payload=b"AA", file_id="abc")
    # Same id, different name: Streamlit says it is the same upload.
    second = FakeUpload(name="renamed.xlsx", payload=b"AA", file_id="abc")
    assert sidebar._newly_uploaded(first, S.SITES_FP) == b"AA"
    assert sidebar._newly_uploaded(second, S.SITES_FP) is None


def test_no_upload_is_not_a_change():
    assert sidebar._newly_uploaded(None, S.SITES_FP) is None


def test_the_two_uploaders_track_separately():
    sites = FakeUpload(name="sites.xlsx", payload=b"SITES")
    planner = FakeUpload(name="planner.xlsx", payload=b"PLAN")
    assert sidebar._newly_uploaded(sites, S.SITES_FP) == b"SITES"
    # A sites upload must not mask a planner upload.
    assert sidebar._newly_uploaded(planner, S.MP_FP) == b"PLAN"
    assert sidebar._newly_uploaded(sites, S.SITES_FP) is None


# ---------------------------------------------------------------------------
# Results must survive a rerun
# ---------------------------------------------------------------------------

def test_results_survive_reruns_while_a_file_stays_loaded():
    """What the planner actually reported: the comparison lost the optimizer run."""
    upload = FakeUpload()
    sidebar._newly_uploaded(upload, S.SITES_FP)

    st.session_state[S.OPT_RESULT] = "a plan"
    # Several reruns later, with the file still sitting in the uploader.
    for _ in range(3):
        if sidebar._newly_uploaded(upload, S.SITES_FP) is not None:
            S.clear_results(st.session_state)

    assert st.session_state[S.OPT_RESULT] == "a plan"


def test_a_genuinely_new_file_does_clear_results():
    """Stale results must not outlive the file they came from."""
    sidebar._newly_uploaded(FakeUpload(name="a.xlsx", payload=b"AAA"), S.SITES_FP)
    st.session_state[S.OPT_RESULT] = "a plan"

    if sidebar._newly_uploaded(
        FakeUpload(name="b.xlsx", payload=b"BBBB"), S.SITES_FP
    ) is not None:
        S.clear_results(st.session_state)

    assert st.session_state[S.OPT_RESULT] is None


# ---------------------------------------------------------------------------
# Accessors the tabs rely on
# ---------------------------------------------------------------------------

def test_loaded_flags_reflect_session_state():
    assert not sidebar.sites_loaded()
    assert not sidebar.planner_loaded()
    st.session_state[S.SITES_BYTES] = b"X"
    st.session_state[S.MP_BYTES] = b"Y"
    assert sidebar.sites_loaded()
    assert sidebar.planner_loaded()


def test_sheet_accessors_fall_back_to_sensible_defaults():
    assert sidebar.sites_sheet() == "Sites"
    assert sidebar.planner_sheet() == "Schedule"
    st.session_state[S.SITES_SHEET] = "Sheet1"
    st.session_state[S.MP_SHEET] = "Plan"
    assert sidebar.sites_sheet() == "Sheet1"
    assert sidebar.planner_sheet() == "Plan"


def test_require_passes_when_everything_is_loaded():
    st.session_state[S.SITES_BYTES] = b"X"
    st.session_state[S.MP_BYTES] = b"Y"
    assert sidebar.require(sites=True, planner=True)


def test_require_fails_when_something_is_missing():
    st.session_state[S.SITES_BYTES] = b"X"
    assert not sidebar.require(sites=True, planner=True)
    assert sidebar.require(sites=True)


# ---------------------------------------------------------------------------
# Deriving must not be undone by the file still sitting in the uploader
# ---------------------------------------------------------------------------

def test_deriving_survives_the_rerun_with_an_upload_still_present():
    """A subtle interaction between the two mechanisms.

    Deriving reruns so the sections above it see the new file. If the uploader's
    fingerprint were cleared at that point, the file still held by the uploader
    would look new on that rerun and overwrite what was just derived.
    """
    upload = FakeUpload(name="mine.xlsx", payload=b"UPLOADED")
    assert sidebar._newly_uploaded(upload, S.SITES_FP) == b"UPLOADED"
    st.session_state[S.SITES_BYTES] = b"UPLOADED"

    # The derive action replaces the loaded file but leaves the fingerprint.
    st.session_state[S.SITES_BYTES] = b"DERIVED"
    st.session_state[S.SITES_FROM_PLANNER] = True

    # The rerun that follows: the uploader still offers its file.
    data = sidebar._newly_uploaded(upload, S.SITES_FP)
    assert data is None, "the upload must not overwrite the derived file"
    assert st.session_state[S.SITES_BYTES] == b"DERIVED"
