#!/usr/bin/env python3
"""Build a Quantumult X remote-rule bundle from ddgksf2013's live indexes.

This file only aggregates upstream URLs; it does not copy or rehost third-party
rule/script source. QX downloads each remote resource according to its interval.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = (
    ROOT / "downloads" / "QuantumultX-Moyu-Rules.conf",
    ROOT / "site" / "downloads" / "QuantumultX-Moyu-Rules.conf",
)

README_URL = "https://raw.githubusercontent.com/ddgksf2013/ddgksf2013/main/README.md"
PROFILE_URL = "https://ddgksf2013.top/Profile/QuantumultX.conf"
FILTER_TREE_URL = "https://api.github.com/repos/ddgksf2013/Filter/git/trees/master?recursive=1"
FILTER_RAW = "https://raw.githubusercontent.com/ddgksf2013/Filter/master/"

# Explicitly omitted: upstream warns that anti-ip is not recommended; the WeChat
# list itself says it should not be used outside Surge; TikTok was removed from
# the current QX profile because it conflicts with Toutiao-related traffic.
FILTER_SKIP = {"anti-ip-attribution.list", "TikTok.list"}
FILTER_SURGE_ONLY_WECHAT_URL = "https://raw.githubusercontent.com/ddgksf2013/Filter/master/WeChat.list"
FILTER_POLICY = {
    "Unbreak.list": "direct",
    "BlockiOSUpdate.list": "reject",
}
FILTER_TAG = {
    "AppleIntelligence.list": "Apple Intelligence",
    "BlockiOSUpdate.list": "Block iOS Update",
    "Emby.list": "Emby",
    "GoogleVoice.list": "Google Voice",
    "NeteaseMusic.list": "Netease Music",
    "Streaming.list": "Streaming Media",
    "StreamingSE.list": "Bilibili Streaming",
    "Unbreak.list": "Unbreak",
}
# The current official profile prefers this maintained Lite variant over the
# older full Bilibili table link. Keep one to avoid overlapping rewrites.
OBSOLETE_ALIAS_URLS = {"https://ddgksf2013.top/rewrite/BiliBiliAds.conf"}


def fetch_text(url: str) -> str:
    req = Request(url, headers={
        "User-Agent": "749894867-adblock-center/1.0",
        "Accept": "application/vnd.github+json, text/plain, */*",
    })
    with urlopen(req, timeout=45) as response:
        data = response.read(12_000_000 + 1)
    if len(data) > 12_000_000:
        raise ValueError(f"upstream document too large: {url}")
    return data.decode("utf-8-sig")


class ReadmeTableParser(HTMLParser):
    """Read active links from README HTML table rows; skip struck-through rows."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self.in_row = False
        self.cells: list[str] = []
        self.cell_parts: list[str] | None = None
        self.links: list[tuple[str, str]] = []
        self.anchor: dict | None = None
        self.struck = False

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)
        tag = tag.lower()
        if tag == "tr":
            self.in_row = True
            self.cells = []
            self.links = []
            self.struck = False
        elif self.in_row and tag in {"s", "del"}:
            self.struck = True
        elif self.in_row and tag in {"td", "th"}:
            self.cell_parts = []
        elif self.in_row and tag == "a":
            self.anchor = {"href": attrs_dict.get("href", ""), "text": []}
        elif self.in_row and tag == "br" and self.cell_parts is not None:
            self.cell_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self.in_row and self.cell_parts is not None:
            self.cell_parts.append(data)
        if self.in_row and self.anchor is not None:
            self.anchor["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self.anchor is not None:
            href = self.anchor["href"].strip()
            label = " ".join("".join(self.anchor["text"]).split())
            if href:
                self.links.append((href, label))
            self.anchor = None
        elif tag in {"td", "th"} and self.cell_parts is not None:
            self.cells.append(" ".join("".join(self.cell_parts).split()))
            self.cell_parts = None
        elif tag == "tr" and self.in_row:
            if self.links:
                self.rows.append({
                    "cells": self.cells[:],
                    "links": self.links[:],
                    "struck": self.struck,
                })
            self.in_row = False
            self.cell_parts = None
            self.anchor = None


def section_lines(profile: str, section_name: str) -> list[str]:
    active = False
    result: list[str] = []
    wanted = section_name.lower()
    for line in profile.splitlines():
        match = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if match:
            if active:
                break
            active = match.group(1).strip().lower() == wanted
            continue
        if active:
            result.append(line.rstrip())
    if not result:
        raise ValueError(f"section [{section_name}] not found in upstream profile")
    return result


def first_url(line: str) -> str:
    return line.split(",", 1)[0].strip()


def parse_options(line: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for part in line.split(",")[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            options[key.strip()] = value.strip()
    return options


def rule_policy(url: str) -> str:
    value = url.lower()
    if "unbreak" in value or "asn.china" in value:
        return "direct"
    if "adrules" in value or "blockiosupdate" in value:
        return "reject"
    return "PROXY"


def make_filter_line(url: str, tag: str, policy: str, opt_parser: str = "true",
                     img_url: str = "") -> str:
    fields = [url, f"tag={tag}"]
    if img_url:
        fields.append(f"img-url={img_url}")
    fields.extend([
        f"force-policy={policy}",
        "update-interval=86400",
        f"opt-parser={opt_parser}",
        "enabled=true",
    ])
    return ", ".join(fields)


def make_rewrite_line(url: str, tag: str, opt_parser: str) -> str:
    return (
        f"{url}, tag={tag}, update-interval=86400, "
        f"opt-parser={opt_parser}, enabled=true"
    )


def build_rewrite_lines(readme: str, profile: str) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()

    # Preserve the current official profile's curated entries and metadata.
    for raw in section_lines(profile, "rewrite_remote"):
        line = raw.strip()
        if not line or line.startswith(("#", ";")) or not line.startswith("http"):
            continue
        url = first_url(line)
        if url not in seen:
            lines.append(line)
            seen.add(url)

    # Add every active QX-compatible .conf/.js resource in the README tables.
    parser = ReadmeTableParser()
    parser.feed(readme)
    added = 0
    for row in parser.rows:
        if row["struck"]:
            continue
        cells = row["cells"]
        title_index = 2 if len(cells) >= 5 else (1 if len(cells) >= 2 else 0)
        title = cells[title_index] if cells else ""
        title = re.sub(r"\s+", " ", title).strip(" ,;")
        for url, label in row["links"]:
            url = url.strip()
            path = url.split("#", 1)[0].split("?", 1)[0].lower()
            ext_match = re.search(r"\.(conf|js|snippet)$", path)
            if not ext_match:
                # .sgmodule is a different client module format; QX .snippet files are supported resources.
                continue
            if url in seen or url in OBSOLETE_ALIAS_URLS:
                continue
            name = title or label or path.rsplit("/", 1)[-1]
            name = name.replace(",", "·").replace("\n", " ").strip()
            opt_parser = "true" if ext_match.group(1) == "js" else "false"
            lines.append(make_rewrite_line(url, f"{name}@README", opt_parser))
            seen.add(url)
            added += 1

    if not lines:
        raise ValueError("no active rewrite resources found")
    print(f"[QX] rewrite resources: {len(lines)} ({added} added from README)")
    return lines


def build_filter_lines(profile: str, filter_tree: dict) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()

    # Reuse the upstream profile's rule sources, but map its named node groups
    # to generic QX proxy/direct/reject policies so this bundle is not tied to
    # the author's node names or temporary proxy subscription.
    for raw in section_lines(profile, "filter_remote"):
        line = raw.strip()
        if not line or line.startswith(("#", ";")) or not line.startswith("http"):
            continue
        url = first_url(line)
        filename = url.split("?", 1)[0].split("#", 1)[0].rsplit("/", 1)[-1]
        if filename in FILTER_SKIP or url == FILTER_SURGE_ONLY_WECHAT_URL or url in seen:
            continue
        options = parse_options(line)
        tag = options.get("tag", filename.rsplit(".", 1)[0])
        img = options.get("img-url", "")
        source_policy = options.get("force-policy", "").strip().lower()
        if source_policy in {"direct", "reject", "proxy"}:
            policy = "PROXY" if source_policy == "proxy" else source_policy
        else:
            policy = rule_policy(url)
        lines.append(make_filter_line(
            url, tag, policy,
            opt_parser=options.get("opt-parser", "true"),
            img_url=img,
        ))
        seen.add(url)

    # Crawl the complete Filter repo tree so newly added QX .list files are
    # discovered automatically. Exclusions above are deliberate compatibility
    # and safety exceptions, not silently imported.
    tree = filter_tree.get("tree", [])
    if filter_tree.get("truncated"):
        raise ValueError("Filter repository tree is truncated; refusing partial output")

    added = 0
    for node in sorted(tree, key=lambda item: item.get("path", "")):
        if node.get("type") != "blob":
            continue
        path = node.get("path", "")
        if "/" in path or not path.lower().endswith(".list"):
            continue
        filename = path.rsplit("/", 1)[-1]
        if filename in FILTER_SKIP or filename == "WeChat.list":
            continue
        url = FILTER_RAW + path
        if url in seen:
            continue
        tag = FILTER_TAG.get(filename, filename.rsplit(".", 1)[0])
        policy = FILTER_POLICY.get(filename, rule_policy(url))
        lines.append(make_filter_line(url, tag, policy, opt_parser="false"))
        seen.add(url)
        added += 1

    if not lines:
        raise ValueError("no active filter resources found")
    print(f"[QX] filter resources: {len(lines)} ({added} from Filter repo tree)")
    return lines


def main() -> None:
    readme = fetch_text(README_URL)
    profile = fetch_text(PROFILE_URL)
    tree_payload = fetch_text(FILTER_TREE_URL)
    import json
    filter_tree = json.loads(tree_payload)

    rewrite_lines = build_rewrite_lines(readme, profile)
    filter_lines = build_filter_lines(profile, filter_tree)

    output = "\n".join([
        "# Quantumult X — 墨鱼规则合集",
        "# Auto-generated daily from ddgksf2013/ddgksf2013 README + current QX profile + ddgksf2013/Filter.",
        "# This is a rules resource bundle, NOT a full proxy profile; keep your own servers/policies.",
        "# Original rules/scripts remain hosted by their respective authors and are fetched by QX every 24 hours.",
        "# QX-incompatible .sgmodule, deprecated rows, anti-ip-attribution, TikTok and the Surge-only Filter/WeChat.list are excluded.",
        "# The current profile's temporary server subscription and author-specific node groups are not copied.",
        "",
        "[rewrite_remote]",
        *rewrite_lines,
        "",
        "[filter_remote]",
        *filter_lines,
        "",
    ])

    for path in OUTPUTS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8", newline="\n")
    print(f"[DONE] wrote {len(output.encode('utf-8'))} bytes to {len(OUTPUTS)} locations")


if __name__ == "__main__":
    main()
