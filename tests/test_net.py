"""Tests for duckdb_extensions.net: one retry policy for every request."""

import http.client
import io
import urllib.error

import pytest

import duckdb_extensions.net as net


class _Response(io.BytesIO):
    def __init__(self, status, body=b""):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(req, code):
    return urllib.error.HTTPError(req.full_url, code, "error", {}, io.BytesIO())


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(net.time, "sleep", lambda s: None)


def _urlopen(monkeypatch, answers):
    """Install a fake urlopen that pops one answer per call: a response, or an exception to raise."""
    calls = []

    def urlopen(req, timeout):
        calls.append(req)
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(net.urllib.request, "urlopen", urlopen)
    return calls


class TestHead:
    def test_200_and_user_agent(self, monkeypatch):
        calls = _urlopen(monkeypatch, [_Response(200)])
        assert net.head("https://example.invalid/x") == 200
        assert calls[0].get_method() == "HEAD"
        assert calls[0].get_header("User-agent") == net.USER_AGENT

    def test_404_without_retry(self, monkeypatch):
        calls = _urlopen(monkeypatch, [urllib.error.HTTPError("https://example.invalid/x", 404, "nf", {}, io.BytesIO())])
        assert net.head("https://example.invalid/x") == 404
        assert len(calls) == 1

    def test_dropped_connection_is_retried(self, monkeypatch, no_sleep):
        calls = _urlopen(monkeypatch, [http.client.RemoteDisconnected(), ConnectionResetError(), _Response(200)])
        assert net.head("https://example.invalid/x") == 200
        assert len(calls) == 3

    def test_persistent_5xx_raises_after_retries(self, monkeypatch, no_sleep):
        err = urllib.error.HTTPError("https://example.invalid/x", 503, "err", {}, io.BytesIO())
        calls = _urlopen(monkeypatch, [err, err, err])
        with pytest.raises(RuntimeError, match="503"):
            net.head("https://example.invalid/x")
        assert len(calls) == net.RETRIES


class TestGetJson:
    def test_parses_and_sends_headers(self, monkeypatch):
        calls = _urlopen(monkeypatch, [_Response(200, b'{"a": 1}')])
        assert net.get_json("https://example.invalid/j", "application/json", token="t") == {"a": 1}
        assert calls[0].get_header("Accept") == "application/json"
        assert calls[0].get_header("Authorization") == "Bearer t"

    def test_404_raises_http_error(self, monkeypatch):
        _urlopen(monkeypatch, [urllib.error.HTTPError("https://example.invalid/j", 404, "nf", {}, io.BytesIO())])
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            net.get_json("https://example.invalid/j", "application/json")
        assert excinfo.value.code == 404
        excinfo.value.close()

    def test_timeout_then_success(self, monkeypatch, no_sleep):
        calls = _urlopen(monkeypatch, [TimeoutError(), _Response(200, b"[]")])
        assert net.get_json("https://example.invalid/j", "application/json") == []
        assert len(calls) == 2


class TestGetBytes:
    def test_body(self, monkeypatch):
        _urlopen(monkeypatch, [_Response(200, b"binary")])
        assert net.get_bytes("https://example.invalid/b") == b"binary"

    def test_404_is_none(self, monkeypatch):
        _urlopen(monkeypatch, [urllib.error.HTTPError("https://example.invalid/b", 404, "nf", {}, io.BytesIO())])
        assert net.get_bytes("https://example.invalid/b") is None

    def test_5xx_then_success(self, monkeypatch, no_sleep):
        err = urllib.error.HTTPError("https://example.invalid/b", 502, "err", {}, io.BytesIO())
        calls = _urlopen(monkeypatch, [err, _Response(200, b"binary")])
        assert net.get_bytes("https://example.invalid/b") == b"binary"
        assert len(calls) == 2
