import pandas as pd
import pytest
import requests

import base
from base import ApiClient, BaseExtractor, BaseTransformer, Pipeline


# ---------- fakes: a tiny integration that proves the pipeline is generic ----------

class FakeExtractor(BaseExtractor):
    name = "fake"

    def fetch_item_ids(self):
        return [1, 2, 3]

    def fetch_item_detail(self, item_id):
        return {"id": item_id}


class ExplodingExtractor(FakeExtractor):
    def fetch_item_ids(self):
        raise AssertionError("network must not be touched when skipping extract")


class FakeTransformer(BaseTransformer):
    sort_field = "id"
    sort_reverse = False

    def transform_item(self, raw):
        return {"id": raw["id"], "double": raw["id"] * 2}


def test_pipeline_runs_end_to_end(tmp_path):
    extractor = FakeExtractor(output_root=str(tmp_path))
    Pipeline(extractor, FakeTransformer(), "fake_report.csv").run()

    assert (tmp_path / "extracted/fake/ids.json").exists()
    assert (tmp_path / "extracted/fake/details.json").exists()
    assert (tmp_path / "transformed/fake/rows.json").exists()
    df = pd.read_csv(tmp_path / "reports/fake_report.csv")
    assert list(df["double"]) == [2, 4, 6]


def test_skip_extract_reuses_saved_data(tmp_path):
    Pipeline(FakeExtractor(output_root=str(tmp_path)), FakeTransformer(), "r.csv").run()

    rows = Pipeline(ExplodingExtractor(output_root=str(tmp_path)),
                    FakeTransformer(), "r.csv").run(skip_extract=True)
    assert [r["id"] for r in rows] == [1, 2, 3]


def test_base_classes_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        BaseExtractor()
    with pytest.raises(TypeError):
        BaseTransformer()


def test_transform_all_skips_empty_items():
    rows = FakeTransformer().transform_all([{"id": 1}, None, {"id": 2}])
    assert [r["id"] for r in rows] == [1, 2]


# ---------- ApiClient ----------

def make_response(status, headers=None):
    r = requests.Response()
    r.status_code = status
    r.headers.update(headers or {})
    r._content = b"{}"
    return r


def client_with(monkeypatch, responses):
    """ApiClient whose session returns the given responses in order."""
    client = ApiClient()
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        return responses[len(calls) - 1]

    monkeypatch.setattr(client.session, "request", fake_request)
    return client, calls


def test_401_raises_permission_error(monkeypatch):
    client, _ = client_with(monkeypatch, [make_response(401)])
    with pytest.raises(PermissionError):
        client.request("GET", "http://x")


def test_plain_403_is_not_retried(monkeypatch):
    client, calls = client_with(monkeypatch, [make_response(403)])
    with pytest.raises(requests.HTTPError):
        client.request("GET", "http://x")
    assert len(calls) == 1


def test_retries_on_503_then_succeeds(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    client, calls = client_with(monkeypatch, [make_response(503), make_response(200)])
    assert client.request("GET", "http://x").status_code == 200
    assert len(calls) == 2


def test_429_waits_for_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr(base.time, "sleep", sleeps.append)
    client, _ = client_with(
        monkeypatch, [make_response(429, {"Retry-After": "3"}), make_response(200)]
    )
    client.request("GET", "http://x")
    assert sleeps == [3]
