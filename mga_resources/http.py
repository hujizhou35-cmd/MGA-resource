"""Bounded HTTP reads for cloud collection; credentials never cross hosts."""
from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any


class SourceAccessError(RuntimeError):
    def __init__(self, url: str, message: str, status: int | None = None):
        self.url, self.status, self.status_code = url, status, status
        super().__init__(f"{url}: {message}" + (f" (HTTP {status})" if status else ""))


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            redirected.remove_header("Authorization")
        return redirected


class HttpClient:
    """One client per source/job. No automatic retry of access denials.

    Files stay in caller-selected cloud storage. Archive bytes are cached in
    memory once per job rather than downloading a ZIP for every member.
    """

    def __init__(self, *, timeout: int = 60, min_interval: float = 0.10,
                 max_file_bytes: int = 64 * 1024 * 1024,
                 token: str | None = None):
        self.timeout = timeout
        self.min_interval = min_interval
        self.max_file_bytes = max_file_bytes
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self._thread_local = threading.local()
        self._request_lock = threading.Lock()
        self._cache_lock = threading.RLock()
        self._stop = threading.Event()
        self._denial: tuple[str, int] | None = None
        self._last_request = 0.0
        self._not_before = 0.0
        self._json_cache: dict[str, Any] = {}
        self._archive_cache: dict[str, bytes] = {}
        self._blocked = {"https://assets.biocartoon.com/catalog/assets.sqlite.gz"}
        self.events: list[dict] = []

    @property
    def opener(self):
        # Handler instances are never shared by simultaneous requests.
        if not hasattr(self._thread_local, "opener"):
            self._thread_local.opener = urllib.request.build_opener(_SafeRedirect())
        return self._thread_local.opener

    @opener.setter
    def opener(self, value):
        self._thread_local.opener = value

    def _reserve_request(self, url: str) -> None:
        """Space starts across all workers; no lock is held during network IO."""
        while True:
            with self._request_lock:
                if self._denial:
                    denied_url, status = self._denial
                    raise SourceAccessError(url, f"source acquisition stopped after access denial at {denied_url}", status)
                if url in self._blocked:
                    raise SourceAccessError(url, "access previously denied; no automatic retry", 403)
                now = time.monotonic()
                pause = max(self._last_request + self.min_interval, self._not_before) - now
                if pause <= 0:
                    self._last_request = now
                    return
            self._stop.wait(pause)

    def _record_event(self, event: dict) -> None:
        with self._request_lock:
            self.events.append(event)

    def _deny(self, url: str, status: int) -> None:
        with self._request_lock:
            self._blocked.add(url)
            self._denial = (url, status)
            self._stop.set()

    def _cooldown(self, delay: float) -> None:
        with self._request_lock:
            self._not_before = max(self._not_before, time.monotonic() + delay)

    @staticmethod
    def _retry_delay(value: str, attempt: int) -> float:
        if value.strip().isdigit():
            return float(value.strip())
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 2.0 * (attempt + 1)

    def get_bytes(self, url: str, *, max_bytes: int | None = None) -> bytes:
        return self._request(url, max_bytes=max_bytes)

    def post_text(self, url: str, payload: str, *, headers: dict[str, str],
                  max_bytes: int = 16 * 1024 * 1024) -> str:
        """Bounded read-only website search action; never used to mutate a source."""
        permitted = {"accept", "content-type", "next-action"}
        if any(key.lower() not in permitted for key in headers):
            raise ValueError("Unexpected search-action header")
        return self._request(url, data=payload.encode("utf-8"), extra_headers=headers,
                             max_bytes=max_bytes).decode("utf-8")

    def _request(self, url: str, *, max_bytes: int | None = None,
                 data: bytes | None = None, extra_headers: dict[str, str] | None = None) -> bytes:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise SourceAccessError(url, "only absolute HTTPS source URLs are accepted")
        limit = self.max_file_bytes if max_bytes is None else max_bytes
        headers = {"User-Agent": "MGA-resource/0.1 (+https://github.com/hujizhou35-cmd/MGA-resource)"}
        headers.update(extra_headers or {})
        if parsed.hostname == "api.github.com":
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
        for attempt in range(3):
            self._reserve_request(url)
            try:
                request = urllib.request.Request(url, headers=headers, data=data)
                with self.opener.open(request, timeout=self.timeout) as response:
                    length = response.headers.get("Content-Length")
                    if length and int(length) > limit:
                        raise SourceAccessError(url, f"response exceeds {limit} byte limit")
                    response_data = response.read(limit + 1)
                    if len(response_data) > limit:
                        raise SourceAccessError(url, f"response exceeds {limit} byte limit")
                    self._record_event({"url": url, "status": response.status, "bytes": len(response_data)})
                    return response_data
            except urllib.error.HTTPError as exc:
                self._record_event({"url": url, "status": exc.code})
                if exc.code in (401, 403, 451):
                    self._deny(url, exc.code)
                    raise SourceAccessError(url, "access denied; not retried", exc.code) from exc
                if exc.code in (429, 500, 502, 503, 504):
                    delay = self._retry_delay(exc.headers.get("Retry-After", ""), attempt)
                    # A server's Retry-After is honored in full, across workers.
                    # No other file starts while this source asks us to wait.
                    self._cooldown(delay)
                    if attempt < 2:
                        continue
                raise SourceAccessError(url, "request failed", exc.code) from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt < 2:
                    self._stop.wait(2.0 * (attempt + 1))
                    continue
                raise SourceAccessError(url, type(exc).__name__) from exc
        raise SourceAccessError(url, "request attempts exhausted")

    def get_text(self, url: str, *, max_bytes: int = 16 * 1024 * 1024) -> str:
        return self.get_bytes(url, max_bytes=max_bytes).decode("utf-8-sig")

    def get_json(self, url: str, *, max_bytes: int = 32 * 1024 * 1024) -> Any:
        with self._cache_lock:
            if url not in self._json_cache:
                self._json_cache[url] = json.loads(self.get_text(url, max_bytes=max_bytes))
            return self._json_cache[url]

    def get_archive(self, url: str, checksum: str | None = None) -> zipfile.ZipFile:
        with self._cache_lock:
            if url not in self._archive_cache:
                data = self.get_bytes(url, max_bytes=512 * 1024 * 1024)
                if checksum:
                    algorithm, expected = checksum.split(":", 1)
                    if algorithm not in ("md5", "sha256"):
                        raise SourceAccessError(url, "unsupported archive checksum")
                    observed = hashlib.new(algorithm, data).hexdigest()
                    if observed.lower() != expected.lower():
                        raise SourceAccessError(url, "archive checksum mismatch")
                self._archive_cache[url] = data
            data = self._archive_cache[url]
        return zipfile.ZipFile(io.BytesIO(data))

    def iter_files(self, descriptors, workers: int = 4):
        """Cloud collection: bounded prefetch, original order, serial consumer.

        Yields (descriptor, content, error). At most four originals are pending;
        the caller processes one result before advancing this iterator. Existing
        get_file/get_bytes interfaces and serial metadata discovery stay valid.
        """
        if not isinstance(workers, int) or not 1 <= workers <= 4:
            raise ValueError("workers must be between 1 and 4")
        remaining = iter(descriptors)
        pending = deque()
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mga-download")

        def fill_one():
            descriptor = next(remaining, None)
            if descriptor is not None:
                pending.append((descriptor, executor.submit(self.get_file, descriptor)))

        try:
            for _ in range(workers):
                fill_one()
            while pending:
                descriptor, future = pending.popleft()
                try:
                    content, error = future.result(), None
                except Exception as exc:
                    content, error = None, exc
                del future
                yield descriptor, content, error
                content = None
                fill_one()
        finally:
            for _, future in pending:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)

    def get_file(self, descriptor: dict) -> bytes:
        """Fetch one original, including originals only distributed inside ZIPs."""
        if descriptor.get("archive_member"):
            with self.get_archive(descriptor["url"], descriptor.get("archive_checksum")) as archive:
                member = archive.getinfo(descriptor["archive_member"])
                if member.is_dir() or member.file_size > self.max_file_bytes:
                    raise SourceAccessError(descriptor["url"], "archive member is not a bounded image")
                data = archive.read(member)
        else:
            data = self.get_bytes(descriptor["url"])
        expected = descriptor.get("upstream_hash")
        if expected and expected.startswith("git-blob-sha1:"):
            observed = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if observed != expected.split(":", 1)[1]:
                raise SourceAccessError(descriptor["url"], "pinned Git blob hash mismatch")
        return data
