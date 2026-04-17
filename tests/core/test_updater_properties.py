"""Property-based tests for pure helpers in yaya.core.updater.

Property tests complement example tests: they search a generated input
space for counterexamples to invariants we state here. When one fails,
hypothesis shrinks it to a minimal reproducer.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from yaya.core import updater

pytestmark = pytest.mark.unit


@given(
    major=st.integers(min_value=0, max_value=10_000),
    minor=st.integers(min_value=0, max_value=10_000),
    patch=st.integers(min_value=0, max_value=10_000),
)
def test_semver_tuple_roundtrip_identity(major: int, minor: int, patch: int) -> None:
    assert updater.semver_tuple(f"{major}.{minor}.{patch}") == (major, minor, patch)


@given(
    major=st.integers(min_value=0, max_value=10_000),
    minor=st.integers(min_value=0, max_value=10_000),
    patch=st.integers(min_value=0, max_value=10_000),
)
def test_semver_tuple_v_prefix_equivalent(major: int, minor: int, patch: int) -> None:
    plain = updater.semver_tuple(f"{major}.{minor}.{patch}")
    prefixed = updater.semver_tuple(f"v{major}.{minor}.{patch}")
    assert plain == prefixed


@given(
    a=st.tuples(
        st.integers(min_value=0, max_value=1_000),
        st.integers(min_value=0, max_value=1_000),
        st.integers(min_value=0, max_value=1_000),
    ),
    b=st.tuples(
        st.integers(min_value=0, max_value=1_000),
        st.integers(min_value=0, max_value=1_000),
        st.integers(min_value=0, max_value=1_000),
    ),
)
def test_semver_tuple_ordering_is_total(a: tuple[int, int, int], b: tuple[int, int, int]) -> None:
    sa = updater.semver_tuple(f"{a[0]}.{a[1]}.{a[2]}")
    sb = updater.semver_tuple(f"{b[0]}.{b[1]}.{b[2]}")
    # tuple comparison is total; our parser must preserve it
    assert (sa < sb) == (a < b)
    assert (sa == sb) == (a == b)


@given(garbage=st.text(min_size=0, max_size=32))
def test_semver_tuple_never_crashes(garbage: str) -> None:
    # Any string in, a 3-tuple of ints out. No exceptions.
    result = updater.semver_tuple(garbage)
    assert isinstance(result, tuple)
    assert len(result) == 3
    assert all(isinstance(x, int) for x in result)


@given(version=st.from_regex(r"[0-9]+\.[0-9]+\.[0-9]+", fullmatch=True).filter(lambda s: len(s) <= 32))
def test_skip_roundtrip_for_semver(version: str) -> None:
    updater.skip_version(version)
    assert updater.is_skipped(version)
