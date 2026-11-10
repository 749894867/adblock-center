#!/usr/bin/env python3
"""Build a small Apple Intelligence/Siri routing module from Apple's host list.

The official Apple support page is the primary source. If Apple changes its page
layout or is temporarily unavailable, the last verified official host list is
used so the ad-block subscription can still be built and deployed.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://support.apple.com/en-us/101555"
POLICY_GROUP = os.environ.get("APPLE_INTELLIGENCE_POLICY", "默认").strip()

OUTPUTS = [
    ROOT / "site" / "downloads" / "Apple-Intelligence-Route.sgmodule",
    ROOT / "downloads" / "Apple-Intelligence-Route.sgmodule",
]
MANIFEST = ROOT / "site" / "data" / "manifest.json"

# Current hosts documented by Apple under "Apple Intelligence, Siri, and Search".
# This is a fallback only; the script attempts to read the current page every run.
OFFICIAL_FALLBACK_HOSTS = {
    "guzzoni.apple.com",
    "*.smoot.apple.com",
    "apple-relay.cloudflare.com",
    "apple-relay.fastly-edge.com",
    "cp4.cloudflare.com",
    "apple-relay.apple.com",
}

# Preserve the repository's existing compatibility hosts. They are not claimed
# to be the complete Apple Intelligence host list published by Apple.
COMPATIBILITY_HOSTS = {
    "gateway.icloud.com",
    "apple-relay.akamaized.net",
    "apple-relay.mask.apple-dns.net",
    "api-siri-prod.apple.com",
    "gspe1-ssl.ls.apple.com",
}

HOST_RE = re.compile(
    r"(?<![A-Za-z0-9*.-])(?:\\*\\.)?"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\\.)+"
    r"[A-Za-z]{2,}(?![A-Za-z0-9.-])"
)


class VisibleTextParser(HTMLParser):
    """Extract visible text from HTML without third-party dependencies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)


def fetch_html() -> str:
    request = Request(
        SOURCE_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AdBlock-Center/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=25) as response:
        raw = response.read(5_000_001)
    if len(raw) > 5_000_000:
        raise ValueError("Apple support page exceeded the 5 MB safety limit")
    return raw.decode("utf-8", errors="replace")


def extract_official_hosts(html: str) -> set[str]:
    parser = VisibleTextParser()
    parser.feed(html)
    text = " ".join(parser.parts)

    heading = re.search(
        r"Apple\\s+Intelligence,\\s*Siri,\\s*and\\s*Search",
        text,
        flags=re.IGNORECASE,
    )
    if not heading:
        raise ValueError("Could not find Apple's Apple Intelligence host section")

    end_positions = [
        pos for marker in (
            "HTTP proxy",
            "Content Distribution Networks and DNS Resolution",
            "Associated Domains",
        )
        if (pos := text.lower().find(marker.lower(), heading.end())) >= 0
    ]
    if not end_positions:
        raise ValueError("Could not determine the end of Apple's host section")

    section = text[heading.end():min(end_positions)]
    hosts = {
        match.group(0).lower().rstrip(".")
        for match in HOST_RE.finditer(section)
    }
    # Keep only the host entries in the official table, not URLs from unrelated
    # page navigation or text outside the Apple Intelligence section.
    hosts = {
        host for host in hosts
        if "." in host.lstrip("*.") and not host.endswith(".html")
    }

    required = {"guzzoni.apple.com", "apple-relay.apple.com"}
    if not required.issubset(hosts):
        raise ValueError(
            "Apple host section was found, but expected key hosts were missing"
        )
    return hosts


def load_hosts() -> tuple[set[str], str]:
    try:
        hosts = extract_official_hosts(fetch_html())
        print(f"[APPLE] Parsed {len(hosts)} hosts from Apple's current support page")
        return hosts, "live"
    except Exception as exc:
        print(f"[WARN] Apple host extraction failed ({exc}); using verified fallback")
        return set(OFFICIAL_FALLBACK_HOSTS), "fallback"


def to_rule(host: str) -> str:
    if host.startswith("*."):
        return f"DOMAIN-SUFFIX,{host[2:]},{POLICY_GROUP}"
    return f"DOMAIN,{host},{POLICY_GROUP}"


def main() -> None:
    if not POLICY_GROUP or "," in POLICY_GROUP or "\\n" in POLICY_GROUP:
        raise SystemExit("APPLE_INTELLIGENCE_POLICY must be a non-empty policy name without commas/newlines")

    official_hosts, source_mode = load_hosts()
    hosts = official_hosts | COMPATIBILITY_HOSTS
    ordered_hosts = sorted(hosts, key=str.casefold)
    rules = [to_rule(host) for host in ordered_hosts]

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    module = [
        "#!name=Apple Intelligence / Siri 分流（每日自动更新）",
        "#!desc=每日同步 Apple 官方 Apple Intelligence、Siri 与 Private Cloud Compute 主机；不启用 MITM。",
        "#!category=Apple",
        "#",
        f"# Generated: {generated_at}",
        f"# Source mode: {source_mode}",
        f"# Host count: {len(ordered_hosts)}",
        f"# Policy group: {POLICY_GROUP}",
        f"# Official source: {SOURCE_URL}",
        "",
        "[Rule]",
        "# Apple 官方文档：Apple Intelligence、Siri、Search 与 Private Cloud Compute",
        *rules,
        "",
        "# 不配置 [MITM]：Apple 官方建议不要对这些加密连接进行 HTTPS 内容检查。",
    ]
    content = "\\n".join(module) + "\\n"
    encoded_size = len(content.encode("utf-8"))
    if encoded_size > 4 * 1024 * 1024:
        raise SystemExit(
            f"Apple route module exceeds the 4 MiB client limit: {encoded_size} bytes"
        )

    for output in OUTPUTS:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")

    manifest = {}
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    manifest["apple_intelligence"] = {
        "generated_at": generated_at,
        "source_url": SOURCE_URL,
        "source_mode": source_mode,
        "policy_group": POLICY_GROUP,
        "host_count": len(ordered_hosts),
        "hosts": ordered_hosts,
        "size_bytes": encoded_size,
        "path": "downloads/Apple-Intelligence-Route.sgmodule",
        "url": "https://749894867.github.io/adblock-center/downloads/Apple-Intelligence-Route.sgmodule",
        "mitm": False,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\\n",
        encoding="utf-8",
    )

    print(f"[APPLE] Wrote {len(ordered_hosts)} hosts to both route-module copies")
    print(f"[APPLE] Module size: {encoded_size} bytes; policy group: {POLICY_GROUP}")


if __name__ == "__main__":
    main()
