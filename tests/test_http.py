import hashlib
import io
import threading
import time
import urllib.error
import zipfile
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate

import pytest

from mga_resources.http import HttpClient, SourceAccessError


class Response(io.BytesIO):
    status = 200

    def __init__(self, payload):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}


def fake_openers(monkeypatch, callback):
    created = []

    class Opener:
        def __init__(self):
            self.owner = threading.get_ident()
            created.append(self.owner)

        def open(self, request, timeout):
            assert self.owner == threading.get_ident()
            return callback(request)

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    return created


def descriptors(count):
    return [{"url": f"https://example.org/{index}.svg"} for index in range(count)]


def test_parallel_downloads_are_bounded_and_yield_original_order(monkeypatch):
    started = []
    gate = threading.Barrier(4)

    def open_file(request):
        index = int(request.full_url.rsplit("/", 1)[1].split(".")[0])
        started.append(index)
        if index < 4:
            gate.wait(timeout=2)
        if index == 0:
            time.sleep(0.02)
        return Response(str(index).encode())

    created = fake_openers(monkeypatch, open_file)
    client = HttpClient(min_interval=0)
    stream = client.iter_files(descriptors(9))
    first = next(stream)
    assert len(started) == 4  # No unbounded eager submission/retention.
    rows = [first, *stream]
    assert [r[1] for r in rows] == [str(i).encode() for i in range(9)]
    assert all(r[2] is None for r in rows)
    assert len(created) == 4
    assert len(client.events) == 9


def test_all_workers_share_request_start_spacing(monkeypatch):
    starts = []

    def open_file(request):
        starts.append(time.monotonic())
        time.sleep(0.025)
        return Response(b"art")

    fake_openers(monkeypatch, open_file)
    list(HttpClient(min_interval=0.025).iter_files(descriptors(5)))
    assert all(b - a >= 0.020 for a, b in zip(starts, starts[1:]))


def test_denial_stops_future_requests_without_retry(monkeypatch):
    requested = []

    def deny(request):
        requested.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 403, "denied", {}, None)

    fake_openers(monkeypatch, deny)
    client = HttpClient(min_interval=0.01)
    rows = list(client.iter_files(descriptors(9)))
    assert len(requested) == 1
    assert len(rows) == 9
    assert all(row[1] is None and isinstance(row[2], SourceAccessError) and row[2].status == 403 for row in rows)
    with pytest.raises(SourceAccessError, match="stopped after access denial"):
        client.get_bytes("https://example.org/another.svg")
    assert len(requested) == 1


def test_retry_after_pauses_all_workers(monkeypatch):
    starts = []
    attempts = {}
    client = HttpClient(min_interval=0.01)
    monkeypatch.setattr(client, "_retry_delay", lambda value, attempt: 0.08)

    def open_file(request):
        url = request.full_url
        starts.append((url, time.monotonic()))
        attempts[url] = attempts.get(url, 0) + 1
        if url.endswith("0.svg") and attempts[url] == 1:
            raise urllib.error.HTTPError(url, 429, "wait", {"Retry-After": "1"}, None)
        return Response(b"art")

    fake_openers(monkeypatch, open_file)
    rows = list(client.iter_files(descriptors(4)))
    assert all(row[2] is None for row in rows)
    assert attempts["https://example.org/0.svg"] == 2
    assert starts[1][1] - starts[0][1] >= 0.07


def test_retry_after_is_not_clamped_and_accepts_http_date():
    assert HttpClient._retry_delay("120", 0) == 120
    assert 55 <= HttpClient._retry_delay(formatdate(time.time() + 60, usegmt=True), 0) <= 60
    assert HttpClient._retry_delay("invalid", 1) == 4


def test_parallel_archive_members_download_archive_once(monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index in range(4):
            archive.writestr(f"{index}.svg", f"svg-{index}")
    payload = buffer.getvalue()
    requested = []

    def open_archive(request):
        requested.append(request.full_url)
        time.sleep(0.02)
        return Response(payload)

    fake_openers(monkeypatch, open_archive)
    client = HttpClient(min_interval=0)
    files = [{"url": "https://example.org/archive.zip", "archive_member": f"{i}.svg",
              "archive_checksum": "sha256:" + hashlib.sha256(payload).hexdigest()} for i in range(4)]
    rows = list(client.iter_files(files))
    assert len(requested) == 1
    assert [row[1] for row in rows] == [f"svg-{i}".encode() for i in range(4)]


def test_parallel_json_cache_downloads_once(monkeypatch):
    requested = []

    def open_json(request):
        requested.append(request.full_url)
        time.sleep(0.01)
        return Response(b'{"title":"art"}')

    fake_openers(monkeypatch, open_json)
    client = HttpClient(min_interval=0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(client.get_json, ["https://example.org/index.json"] * 4))
    assert requested == ["https://example.org/index.json"]
    assert rows == [{"title": "art"}] * 4


def test_single_thread_interface_and_hash_verification_still_work(monkeypatch):
    fake_openers(monkeypatch, lambda request: Response(b"<svg/>"))
    client = HttpClient(min_interval=0)
    digest = hashlib.sha1(b"blob 6\0<svg/>").hexdigest()
    descriptor = {"url": "https://example.org/art.svg", "upstream_hash": "git-blob-sha1:" + digest}
    assert client.get_file(descriptor) == b"<svg/>"
    assert next(client.iter_files([descriptor], workers=1))[1] == b"<svg/>"
    bad = {**descriptor, "upstream_hash": "git-blob-sha1:" + "0" * 40}
    assert isinstance(next(client.iter_files([bad]))[2], SourceAccessError)


@pytest.mark.parametrize("workers", [0, 5, -1, 1.5])
def test_parallel_worker_limit_is_explicit(workers):
    with pytest.raises(ValueError):
        list(HttpClient().iter_files([], workers=workers))
