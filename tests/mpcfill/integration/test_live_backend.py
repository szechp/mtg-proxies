"""Integration tests that hit the live mpcfill.com backend.

Skipped by default. Run manually with:

    uv run pytest -m integration

These exist because unit tests with hand-rolled stubs can't catch schema drift on the live
server — they only confirm parsing logic works against the shape we *assumed* was correct.
The original `sources: null` HTTP 400 bug is a textbook example: every unit test passed
because they fed the parser exactly what the parser expected, never validating that what
we POST is what the upstream Pydantic schema actually accepts.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests


@pytest.mark.integration
def test_live_editor_search_accepts_payload(tmp_path: Path) -> None:
    """Verify that a real `/2/editorSearch/` POST returns HTTP 200 with our request shape."""
    from mtg_proxies.mpcfill.client import search

    session = requests.Session()
    result = search(
        "https://mpcfill.com",
        ["lightning bolt"],
        session=session,
        cache_root=tmp_path,
        use_cache=False,
    )
    assert "lightning bolt" in result
    # Lightning Bolt is one of the most-rendered MTG cards; live backend should have many candidates.
    assert len(result["lightning bolt"]) >= 1, "expected at least one mpcfill candidate for Lightning Bolt"
    candidate = result["lightning bolt"][0]
    assert candidate.drive_id
    assert candidate.name


@pytest.mark.integration
def test_live_sources_endpoint_returns_catalog() -> None:
    """Verify the `/2/sources/` endpoint shape — needed to build the editorSearch sources list."""
    from mtg_proxies.mpcfill.client import list_sources

    session = requests.Session()
    sources = list_sources("https://mpcfill.com", session=session)
    assert len(sources) >= 1, "expected at least one source in the live catalog"
    sample = sources[0]
    assert "pk" in sample
    assert isinstance(sample["pk"], int)
