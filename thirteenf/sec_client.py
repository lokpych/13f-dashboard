"""One shared, polite SEC HTTP client: 5 requests/second, retries, disk cache, retrieval log."""
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone

import requests

import config


class SECNotFound(RuntimeError):
    pass


class _RateLimiter:
    def __init__(self, per_second):
        self.gap = 1.0 / per_second
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self):
        with self.lock:
            pause = self.last + self.gap - time.monotonic()
            if pause > 0:
                time.sleep(pause)
            self.last = time.monotonic()


_LIMITER = _RateLimiter(config.MAX_REQUESTS_PER_SECOND)   # shared by every client in the process


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SECClient:
    def __init__(self, cache_dir=None, user_agent=None):
        self.ua = user_agent or config.user_agent()
        self.cache_dir = config.CACHE_DIR if cache_dir is None else cache_dir
        self.offline = os.environ.get("DEMO_OFFLINE") == "1"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.ua, "Accept-Encoding": "gzip, deflate"})

    # ---- cache layout: documents by accession + document name, discovery by URL hash
    def _path(self, url):
        m = re.search(r"/Archives/edgar/data/\d+/(\d{18})/([^/?]+)$", url)
        if m:
            return self.cache_dir / m.group(1) / m.group(2)
        tag = hashlib.sha256(url.encode()).hexdigest()[:16]
        name = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("/")[-1])[:60]
        return self.cache_dir / "discovery" / f"{tag}_{name}"

    def _fetch(self, url):
        last = None
        for attempt in range(config.MAX_RETRIES):
            _LIMITER.wait()
            try:
                r = self.session.get(url, timeout=config.REQUEST_TIMEOUT)
                if r.status_code == 200:
                    return r.content
                if r.status_code == 404:
                    raise SECNotFound(f"404 Not Found: {url}")
                last = f"HTTP {r.status_code}"
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
            except (requests.ConnectionError, requests.Timeout) as e:
                last = str(e)
            time.sleep(min(8, 2 ** attempt))
        raise RuntimeError(f"SEC request failed after retries ({last}): {url}")

    def get(self, url, refresh=False, max_age=None):
        """Return (bytes, meta). Cached unless refresh=True or the cache is older than max_age seconds."""
        path = self._path(url)
        side = path.with_name(path.name + ".meta.json")
        if path.exists() and side.exists():
            age = time.time() - path.stat().st_mtime
            fresh = (not refresh) and (max_age is None or age <= max_age)
            if fresh or self.offline:
                meta = json.loads(side.read_text())
                meta["source"] = "cache"
                return path.read_bytes(), meta
        if self.offline:
            raise RuntimeError(f"DEMO_OFFLINE=1 and nothing cached for {url}")
        data = self._fetch(url)
        meta = {"url": url, "retrieved_at": now_iso(), "source": "network",
                "access_path": "direct SEC HTTP"}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        side.write_text(json.dumps(meta))
        return data, meta

    def get_json(self, url, **kw):
        data, meta = self.get(url, **kw)
        return json.loads(data), meta

    def submissions(self, cik):
        url = f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json"
        return self.get_json(url, max_age=config.DISCOVERY_MAX_AGE)
