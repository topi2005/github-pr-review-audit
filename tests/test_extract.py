import pytest

from extract import DETAIL_QUERY, LIST_QUERY, GitHubExtractor


def make_extractor(tmp_path, max_items=200):
    return GitHubExtractor("tok", "org", "repo",
                           max_items=max_items, output_root=str(tmp_path))


def list_page(numbers, has_next, cursor=None):
    return {"repository": {"pullRequests": {
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        "nodes": [{"number": n} for n in numbers],
    }}}


class FakeGraphQL:
    """Replaces GitHubExtractor._graphql and records every call."""

    def __init__(self, pages=(), detail=None):
        self.pages = list(pages)
        self.detail = detail or (lambda n: {"number": n})
        self.calls = []

    def __call__(self, query, variables):
        self.calls.append((query, variables))
        if query == LIST_QUERY:
            return self.pages[sum(1 for q, _ in self.calls if q == LIST_QUERY) - 1]
        return {"repository": {"pullRequest": self.detail(variables["number"])}}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


# ---------- construction ----------

def test_missing_token_raises(tmp_path):
    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        GitHubExtractor("", "org", "repo", output_root=str(tmp_path))


def test_token_sent_as_bearer_header(tmp_path):
    ex = make_extractor(tmp_path)
    assert ex.client.session.headers["Authorization"] == "Bearer tok"


# ---------- listing / pagination ----------

def test_single_page_stops_when_no_next_page(tmp_path):
    ex = make_extractor(tmp_path)
    fake = FakeGraphQL(pages=[list_page([3, 2, 1], has_next=False)])
    ex._graphql = fake

    assert ex.fetch_item_ids() == [3, 2, 1]
    assert len(fake.calls) == 1


def test_follows_cursor_across_pages(tmp_path):
    ex = make_extractor(tmp_path)
    fake = FakeGraphQL(pages=[
        list_page([1, 2], has_next=True, cursor="c1"),
        list_page([3], has_next=False),
    ])
    ex._graphql = fake

    assert ex.fetch_item_ids() == [1, 2, 3]
    assert fake.calls[0][1]["cursor"] is None
    assert fake.calls[1][1]["cursor"] == "c1"


def test_max_items_caps_total_and_page_size(tmp_path):
    ex = make_extractor(tmp_path, max_items=130)
    fake = FakeGraphQL(pages=[
        list_page(range(100), has_next=True, cursor="c1"),
        list_page(range(100, 130), has_next=True, cursor="c2"),
    ])
    ex._graphql = fake

    assert len(ex.fetch_item_ids()) == 130
    assert [c[1]["pageSize"] for c in fake.calls] == [100, 30]  # never over-fetches


def test_small_max_items_requests_small_page(tmp_path):
    ex = make_extractor(tmp_path, max_items=3)
    fake = FakeGraphQL(pages=[list_page([1, 2, 3], has_next=True, cursor="c1")])
    ex._graphql = fake

    assert ex.fetch_item_ids() == [1, 2, 3]
    assert len(fake.calls) == 1
    assert fake.calls[0][1]["pageSize"] == 3


def test_empty_repository_returns_no_ids(tmp_path):
    ex = make_extractor(tmp_path)
    ex._graphql = FakeGraphQL(pages=[list_page([], has_next=False)])
    assert ex.fetch_item_ids() == []


def test_list_query_sends_owner_and_repo(tmp_path):
    ex = make_extractor(tmp_path)
    fake = FakeGraphQL(pages=[list_page([1], has_next=False)])
    ex._graphql = fake
    ex.fetch_item_ids()
    variables = fake.calls[0][1]
    assert variables["owner"] == "org" and variables["name"] == "repo"


# ---------- detail ----------

def test_detail_passes_number_and_returns_pull_request(tmp_path):
    ex = make_extractor(tmp_path)
    fake = FakeGraphQL(detail=lambda n: {"number": n, "title": "T"})
    ex._graphql = fake

    assert ex.fetch_item_detail(42) == {"number": 42, "title": "T"}
    query, variables = fake.calls[0]
    assert query == DETAIL_QUERY
    assert variables == {"owner": "org", "name": "repo", "number": 42}


# ---------- _graphql transport ----------

def test_graphql_posts_query_and_returns_data(tmp_path):
    ex = make_extractor(tmp_path)
    sent = {}

    def fake_request(method, url, **kwargs):
        sent.update(method=method, url=url, json=kwargs["json"])
        return FakeResponse({"data": {"ok": True}})

    ex.client.request = fake_request
    assert ex._graphql("query { x }", {"a": 1}) == {"ok": True}
    assert sent["method"] == "POST"
    assert sent["url"] == "https://api.github.com/graphql"
    assert sent["json"] == {"query": "query { x }", "variables": {"a": 1}}


def test_graphql_errors_without_data_raise(tmp_path):
    ex = make_extractor(tmp_path)
    ex.client.request = lambda *a, **k: FakeResponse({"errors": [{"message": "boom"}]})
    with pytest.raises(RuntimeError, match="boom"):
        ex._graphql("q", {})


def test_graphql_partial_errors_with_data_are_tolerated(tmp_path):
    ex = make_extractor(tmp_path)
    ex.client.request = lambda *a, **k: FakeResponse(
        {"data": {"ok": 1}, "errors": [{"message": "minor"}]}
    )
    assert ex._graphql("q", {}) == {"ok": 1}


# ---------- full run ----------

def test_run_writes_ids_and_details(tmp_path):
    ex = make_extractor(tmp_path)
    ex._graphql = FakeGraphQL(pages=[list_page([7, 8], has_next=False)])

    details = ex.run()

    assert [d["number"] for d in details] == [7, 8]
    assert (tmp_path / "extracted/github/ids.json").exists()
    assert (tmp_path / "extracted/github/details.json").exists()
    assert [d["number"] for d in ex.load_cached()] == [7, 8]
