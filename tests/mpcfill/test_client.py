from __future__ import annotations

from pathlib import Path


class _StubResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = "" if payload is None else str(payload)

    def json(self) -> object:
        return self._payload


class _StubSession:
    """In-memory request stub recording every call and the payload sent.

    Both `get` and `post` are wired up; the test records every URL + JSON pair so we
    can assert on the exact shape we POST to the upstream server — that is what would
    have caught the `sources: null` schema regression in the live `/2/editorSearch/`.
    """

    def __init__(self, by_url: dict[str, _StubResponse]) -> None:
        self.by_url = by_url
        self.calls: list[tuple[str, str, dict | None]] = []

    def get(self, url: str, **_: object) -> _StubResponse:
        self.calls.append(("GET", url, None))
        return self.by_url[url]

    def post(self, url: str, *, json: dict, **_: object) -> _StubResponse:
        self.calls.append(("POST", url, json))
        return self.by_url[url]


def _sources_response() -> _StubResponse:
    return _StubResponse(
        200,
        {
            "results": {
                "1": {"pk": 1, "name": "MrTeferi", "key": "MrTeferi"},
                "2": {"pk": 2, "name": "Chilli_Axe", "key": "Chilli_Axe"},
            }
        },
    )


def _make_session_with_one_candidate(server: str = "https://mpcfill.com") -> _StubSession:
    return _StubSession({
        f"{server}/2/sources/": _sources_response(),
        f"{server}/2/editorSearch/": _StubResponse(
            200,
            {"results": {"lightning bolt": {"CARD": ["uuid-1"]}}},
        ),
        f"{server}/2/cards/": _StubResponse(
            200,
            {
                "results": {
                    "uuid-1": {
                        "identifier": "drive-abc",
                        "name": "Lightning Bolt",
                        "sourceName": "Chilli_Axe's MPC Proxies",
                        "priority": 1,
                        "dpi": 800,
                        "size": 1234567,
                        "extension": "png",
                    }
                }
            },
        ),
    })


def test_search_returns_candidate_from_two_step_flow(tmp_path: Path) -> None:
    from mtg_proxies.mpcfill.client import search
    from mtg_proxies.scryfall.rate_limit import RateLimiter

    session = _make_session_with_one_candidate()
    candidates = search(
        "https://mpcfill.com",
        ["lightning bolt"],
        session=session,
        cache_root=tmp_path,
        use_cache=False,
        rate_limiter=RateLimiter(delay=0.0),
    )
    assert list(candidates.keys()) == ["lightning bolt"]
    result = candidates["lightning bolt"]
    assert len(result) == 1
    assert result[0].drive_id == "drive-abc"
    assert result[0].source_name == "Chilli_Axe's MPC Proxies"
    assert result[0].dpi == 800


def test_editor_search_payload_has_non_null_sources_list(tmp_path: Path) -> None:
    """Regression guard: the upstream Pydantic schema rejects `sources: null`.

    Reproducer (would have caught the original bug): a real `/2/editorSearch/` POST returns
    HTTP 400 `list_type` if `searchSettings.sourceSettings.sources` is null or missing.
    This test asserts we send a list of `[pk, bool]` pairs.
    """
    from mtg_proxies.mpcfill.client import search
    from mtg_proxies.scryfall.rate_limit import RateLimiter

    session = _make_session_with_one_candidate()
    search(
        "https://mpcfill.com",
        ["lightning bolt"],
        session=session,
        cache_root=tmp_path,
        use_cache=False,
        rate_limiter=RateLimiter(delay=0.0),
    )
    editor_calls = [c for c in session.calls if c[1].endswith("/2/editorSearch/")]
    assert editor_calls, "expected an editorSearch POST"
    method, _url, payload = editor_calls[0]
    assert method == "POST"
    assert payload is not None
    sources = payload["searchSettings"]["sourceSettings"]["sources"]
    assert isinstance(sources, list), f"sources must be a list, got {type(sources).__name__}"
    assert sources, "sources list must be non-empty (the schema rejects null/empty)"
    for entry in sources:
        assert isinstance(entry, list), f"each entry must be a list, got {entry!r}"
        assert len(entry) == 2, f"each entry must have 2 items [pk, bool], got {entry!r}"
        assert isinstance(entry[0], int), f"pk must be int, got {type(entry[0]).__name__}"
        assert isinstance(entry[1], bool), f"enabled must be bool, got {type(entry[1]).__name__}"


def test_search_fetches_sources_catalog_once_per_run(tmp_path: Path) -> None:
    from mtg_proxies.mpcfill.client import search
    from mtg_proxies.scryfall.rate_limit import RateLimiter

    session = _make_session_with_one_candidate()
    search(
        "https://mpcfill.com",
        ["lightning bolt"],
        session=session,
        cache_root=tmp_path,
        use_cache=False,
        rate_limiter=RateLimiter(delay=0.0),
    )
    sources_calls = [c for c in session.calls if c[1].endswith("/2/sources/")]
    assert len(sources_calls) == 1


def test_search_empty_query_list_short_circuits(tmp_path: Path) -> None:
    from mtg_proxies.mpcfill.client import search

    session = _StubSession({})  # No URL configured; should not be hit.
    assert search("https://mpcfill.com", [], session=session, cache_root=tmp_path, use_cache=False) == {}
    assert session.calls == []


def test_search_filters_excluded_sources(tmp_path: Path) -> None:
    from mtg_proxies.mpcfill.client import search
    from mtg_proxies.scryfall.rate_limit import RateLimiter

    server = "https://mpcfill.com"
    session = _StubSession({
        f"{server}/2/sources/": _sources_response(),
        f"{server}/2/editorSearch/": _StubResponse(
            200,
            {"results": {"lightning bolt": {"CARD": ["a", "b"]}}},
        ),
        f"{server}/2/cards/": _StubResponse(
            200,
            {
                "results": {
                    "a": {"identifier": "1", "name": "X", "sourceName": "Wanted"},
                    "b": {"identifier": "2", "name": "Y", "sourceName": "Blocked"},
                }
            },
        ),
    })
    candidates = search(
        server,
        ["lightning bolt"],
        session=session,
        exclude_sources=["blocked"],
        cache_root=tmp_path,
        use_cache=False,
        rate_limiter=RateLimiter(delay=0.0),
    )
    drive_ids = [c.drive_id for c in candidates["lightning bolt"]]
    assert drive_ids == ["1"]


def test_search_raises_on_non_200(tmp_path: Path) -> None:
    import pytest

    from mtg_proxies.mpcfill.client import search
    from mtg_proxies.mpcfill.errors import SearchError
    from mtg_proxies.scryfall.rate_limit import RateLimiter

    server = "https://mpcfill.com"
    session = _StubSession({
        f"{server}/2/sources/": _sources_response(),
        f"{server}/2/editorSearch/": _StubResponse(503, {}),
    })
    with pytest.raises(SearchError):
        search(
            server,
            ["x"],
            session=session,
            cache_root=tmp_path,
            use_cache=False,
            rate_limiter=RateLimiter(delay=0.0),
        )
