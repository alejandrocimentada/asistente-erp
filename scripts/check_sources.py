#!/usr/bin/env python3
"""Check that every source link in knowledge/ still opens a real page on help.sap.com.

help.sap.com renders its pages with JavaScript, so a plain download only returns an empty
shell. This script asks the portal's own content service for each page and prints its title.
It needs internet access, so it is not part of the automatic tests.

Usage:
    python scripts/check_sources.py            # exit code 1 if any link is broken
"""
from __future__ import annotations

import gzip
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
API = "https://help.sap.com/http.svc"
HEADERS = {"User-Agent": "asistente-erp-source-check", "Accept-Encoding": "gzip"}


def fetch(url: str, extra: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={**HEADERS, **(extra or {})})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read()
    return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body


def page_title(url: str) -> str:
    """Return the page title, or raise if the page does not exist or is empty."""
    parsed = urllib.parse.urlparse(url)
    parts = parsed.path.split("/")
    if parts[1] != "docs":                                  # direct document, e.g. a PDF
        if not fetch(url, {"Range": "bytes=0-1000"}).startswith(b"%PDF"):
            raise ValueError("no es un PDF")
        return "(PDF)"
    product, deliverable, topic = parts[2], parts[3], parts[-1]
    query = {"product_url": product, "deliverable_url": deliverable, "locale": "en-US", "state": "PRODUCTION"}
    version = urllib.parse.parse_qs(parsed.query).get("version")
    if version:
        query["version"] = version[0]
    meta = json.loads(fetch(f"{API}/deliverableMetadata?{urllib.parse.urlencode(query)}"))
    dlv_id = meta["data"]["deliverable"]["id"]
    page = json.loads(fetch(f"{API}/pagecontent?deliverable_id={dlv_id}&file_path={topic}&deliverableInfo=1"))["data"]
    if len(page.get("body") or "") < 200:
        raise ValueError("página vacía")
    return page.get("currentPage", {}).get("t") or "(sin título)"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    urls: dict[str, list[str]] = {}
    for f in sorted(KNOWLEDGE_DIR.rglob("*.yaml")):
        entry = yaml.safe_load(f.read_text(encoding="utf-8"))
        for s in entry["sources"]:
            urls.setdefault(s["url"], []).append(entry["id"])
    broken = 0
    for url, ids in urls.items():
        try:
            print(f"OK    {page_title(url)[:70]}  [{', '.join(ids)}]")
        except Exception as exc:  # noqa: BLE001 - report any failure for this link
            broken += 1
            print(f"ROTO  {url}  [{', '.join(ids)}]  ({exc})")
    print(f"\n{len(urls) - broken} de {len(urls)} enlaces funcionan.")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
