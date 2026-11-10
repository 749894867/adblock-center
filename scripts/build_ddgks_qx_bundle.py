#!/usr/bin/env python3
"""Build a Quantumult X remote-rule bundle from ddgksf2013's live indexes.

This file only aggregates upstream URLs; it does not copy or rehost third-party
rule/script source. QX downloads each remote resource according to its interval.
"""
from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
INSTALL_PAGE = ROOT / "site" / "qx-install.html"
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
    # Build a one-tap QX add-resource link. It appends resources and preserves
    # the existing profile; it does not use update-configuration.
    payload = json.dumps(
        {"rewrite_remote": rewrite_lines, "filter_remote": filter_lines},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    encoded_payload = quote(payload, safe="")
    universal_link = (
        "https://quantumult.app/x/open-app/add-resource?remote-resource="
        + encoded_payload
    )
    custom_scheme_link = (
        "quantumult-x:///add-resource?remote-resource=" + encoded_payload
    )
    INSTALL_PAGE.parent.mkdir(parents=True, exist_ok=True)
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#111827">
  <title>Quantumult X 墨鱼规则合集 · 一键添加</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 680px; margin: 48px auto; padding: 0 20px; color: #172033; line-height: 1.65; }}
    h1 {{ font-size: 25px; }}
    a.button {{ display: block; margin: 18px 0; padding: 14px 18px; border-radius: 12px; background: #1769d2; color: white; text-decoration: none; font-weight: 650; text-align: center; }}
    a.secondary {{ display: block; margin: 14px 0; padding: 12px; border: 1px solid #9ca3af; border-radius: 12px; color: #172033; text-align: center; text-decoration: none; }}
    .note {{ background: #f3f4f6; border-radius: 12px; padding: 14px 16px; }}
    small {{ color: #596579; }}
  </style>
</head>
<body>
  <h1>Quantumult X · 墨鱼规则合集</h1>
  <p>本页将当前生成的远程重写和分流资源添加到 Quantumult X。</p>
  <a class="button" href="{html.escape(universal_link, quote=True)}">一键添加到 Quantumult X</a>
  <a class="secondary" href="{html.escape(custom_scheme_link, quote=True)}">备用方式：使用 Quantumult X URL Scheme</a>
  <div class="note">
    <strong>安全说明</strong><br>
    使用 add-resource 操作，会追加远程资源，不会替换现有代理订阅或主配置。<br>
    每条远程资源设置为每天更新一次。
  </div>
  <p><small>当前资源：{len(rewrite_lines)} 条远程重写、{len(filter_lines)} 条远程分流。若通用链接没有唤起 App，请使用备用方式，或检查 Quantumult X 版本及 iOS 通用链接状态。</small></p>
  <p><a href="https://raw.githubusercontent.com/749894867/adblock-center/main/downloads/QuantumultX-Moyu-Rules.conf">查看原始 .conf 规则索引</a></p>
</body>
</html>
"""
    INSTALL_PAGE.write_text(page, encoding="utf-8", newline="\\n")
    print(f"[DONE] wrote {len(output.encode('utf-8'))} bytes to {len(OUTPUTS)} locations")


if __name__ == "__main__":
    main()
