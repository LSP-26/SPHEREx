from types import SimpleNamespace

import pytest

from backend.offline.target_catalogue import positional_rows, run_async_query


class Resp:
    def __init__(self, text="", status=200, headers=None):
        self.text, self.status_code, self.headers = text, status, headers or {}


class FakeClient:
    def __init__(self, phases, result="uri,t0,t1\n", error="no such column"):
        self.phases, self.result, self.error, self.urls = list(phases), result, error, []

    def post(self, url, data=None):
        self.urls.append(url)
        return Resp(status=303, headers={"Location": "https://irsa.ipac.caltech.edu/TAP/async/j1"})

    def get(self, url):
        self.urls.append(url)
        if url.endswith("/phase"):
            return Resp(self.phases.pop(0) if len(self.phases) > 1 else self.phases[0])
        if url.endswith("/results/result"):
            return Resp(self.result)
        if url.endswith("/error"):
            return Resp(self.error)
        raise AssertionError(url)


def test_async_job_is_polled_until_completed_then_result_fetched():
    sleeps = []
    c = FakeClient(["QUEUED", "EXECUTING", "COMPLETED"], result="uri,t0,t1\na.fits,1,2\n")
    r = run_async_query("SELECT 1", client=c, sleep=sleeps.append)
    assert r.status_code == 200 and r.text == "uri,t0,t1\na.fits,1,2\n"
    assert len(sleeps) == 2
    assert c.urls[-1] == "https://irsa.ipac.caltech.edu/TAP/async/j1/results/result"


def test_job_error_comes_back_as_a_400_with_the_message():
    r = run_async_query("SELECT 1", client=FakeClient(["ERROR"]), sleep=lambda s: None)
    assert r.status_code == 400 and "no such column" in r.text


def test_job_that_never_finishes_is_a_clear_error():
    with pytest.raises(RuntimeError, match="still 'EXECUTING'"):
        run_async_query("SELECT 1", client=FakeClient(["EXECUTING"]), poll_s=2.0,
                        max_wait_s=4.0, sleep=lambda s: None)


def test_server_error_stops_at_once_instead_of_trying_other_columns():
    asked = []

    def post(query):
        asked.append(query)
        return SimpleNamespace(status_code=504, text="Gateway Time-out")

    with pytest.raises(RuntimeError, match="not a column problem"):
        positional_rows(10.0, 5.0, post=post, sleep=lambda s: None)
    assert len(asked) == 3
