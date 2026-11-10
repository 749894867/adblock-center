#!/usr/bin/env python3
"""Fetch public ad-rule sources into site/sources and write a reproducible manifest.

Raw upstream files are archival inputs, not automatically PopCat-compatible modules.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
SOURCE_ROOT = SITE / "sources"
MANIFEST = SITE / "data" / "manifest.json"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
MAX_FILES_PER_SOURCE = 120
MAX_FILE_BYTES = 2_000_000
USER_AGENT = "AdBlock-Center-Upstream-Sync/1.0"

SOURCES = [
    {"id":"ddgksf2013-rewrite","name":"墨鱼 Rewrite","repo":"ddgksf2013/Rewrite","branch":"master","prefixes":["AdBlock/"],"extensions":[".conf",".js",".snippet",".sgmodule",".list"],"category":"APP 专项原始配置"},
    {"id":"cats-team","name":"Cats-Team AdRules","repo":"Cats-Team/AdRules","branch":"main","exact":["adrules-surge.conf"],"category":"通用广告规则"},
    {"id":"awavenue","name":"AWAvenue","repo":"TG-Twilight/AWAvenue-Ads-Rule","branch":"main","exact":["Filters/AWAvenue-Ads-Rule-Surge-RULE-SET.list"],"category":"通用广告规则"},
    {"id":"anti-ad","name":"anti-AD","repo":"privacy-protection-tools/anti-AD","branch":"master","exact":["anti-ad-surge.txt"],"category":"广告与隐私规则"},
    {"id":"blackmatrix7","name":"Blackmatrix7 Advertising","repo":"blackmatrix7/ios_rule_script","branch":"master","exact":["rule/Surge/Advertising/Advertising.list"],"category":"通用广告规则"},
    {"id":"app2smile","name":"app2smile rules","repo":"app2smile/rules","branch":"master","prefixes":["module/"],"extensions":[".conf",".sgmodule",".snippet",".list"],"category":"APP 专项规则"},
]

def request_bytes(url: str) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if TOKEN and "api.github.com" in url:
        headers["Authorization"] = "Bearer " + TOKEN
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=35) as response:
        data = response.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("file exceeds size cap")
    return data

def api_json(url: str):
    return json.loads(request_bytes(url).decode("utf-8"))

def safe_relpath(value: str) -> Path:
    p = Path(value.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        raise ValueError("unsafe source path")
    return p

def save_source(source_id: str, repo: str, branch: str, path: str, data: bytes, page_url: str, category: str):
    rel = Path(source_id) / safe_relpath(path)
    out = SOURCE_ROOT / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return {
        "repo": repo,
        "category": category,
        "path": path,
        "site_path": "sources/" + rel.as_posix(),
        "source_url": page_url,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }

def get_tree(repo: str, branch: str):
    url = f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
    payload = api_json(url)
    return payload.get("tree", []), payload.get("sha", ""), bool(payload.get("truncated"))

def raw_url(repo: str, branch: str, path: str):
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"

def sync_repo_source(spec):
    repo, branch = spec["repo"], spec["branch"]
    entries, errors = [], []
    try:
        tree, tree_sha, truncated = get_tree(repo, branch)
        paths = []
        for node in tree:
            if node.get("type") != "blob":
                continue
            path = node.get("path", "")
            ext = Path(path).suffix.lower()
            if "exact" in spec and path in spec["exact"]:
                paths.append(path)
            elif any(path.startswith(prefix) for prefix in spec.get("prefixes", [])) and ext in spec.get("extensions", []):
                paths.append(path)
        paths = sorted(paths)[:MAX_FILES_PER_SOURCE]
        for path in paths:
            try:
                data = request_bytes(raw_url(repo, branch, path))
                entry = save_source(spec["id"], repo, branch, path, data, f"https://github.com/{repo}/blob/{branch}/{path}", spec["category"])
                entries.append(entry)
            except Exception as exc:
                errors.append(f"{path}: {exc}")
        return entries, {"id":spec["id"],"name":spec["name"],"repo":repo,"branch":branch,"commit":tree_sha,"file_count":len(entries),"tree_truncated":truncated,"errors":errors}
    except Exception as exc:
        return entries, {"id":spec["id"],"name":spec["name"],"repo":repo,"branch":branch,"commit":None,"file_count":0,"tree_truncated":False,"errors":[str(exc)]}

def sync_linked_dependencies(config_entries):
    """Download GitHub raw/Gist dependencies referenced by the synced MuYu configs."""
    url_re = re.compile(r'https?://(?:raw\\.githubusercontent\\.com|gist\\.githubusercontent\\.com)/[^\\s\\"\'<>]+', re.I)
    seen, results = set(), []
    allowed = ("raw.githubusercontent.com", "gist.githubusercontent.com")
    for entry in config_entries:
        try:
            path = SITE / entry["site_path"]
            text = path.read_text("utf-8", errors="ignore")
        except Exception:
            continue
        for match in url_re.findall(text):
            url = match.rstrip("),;]")
            parsed = urlparse(url)
            if parsed.hostname not in allowed or url in seen:
                continue
            seen.add(url)
            if len(results) >= 100:
                return results
            try:
                data = request_bytes(url)
                host = parsed.hostname.replace(".", "_")
                rel = Path("dependencies") / host / safe_relpath(parsed.path.lstrip("/"))
                out = SOURCE_ROOT / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
                results.append({
                    "repo":"linked dependency","category":"脚本依赖原始归档",
                    "path":str(rel),"site_path":"sources/"+rel.as_posix(),
                    "source_url":url,"bytes":len(data),
                    "sha256":hashlib.sha256(data).hexdigest()
                })
            except Exception as exc:
                print(f"[WARN] dependency {url}: {exc}", file=sys.stderr)
    return results

def main():
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "data").mkdir(parents=True, exist_ok=True)
    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    # Avoid stale removed upstream files being mistaken for current files.
    entries, source_rows = [], []
    for spec in SOURCES:
        got, row = sync_repo_source(spec)
        entries.extend(got)
        source_rows.append(row)
        print(f"[SYNC] {row['name']}: {row['file_count']} files")
    rewrite_entries = [e for e in entries if e["repo"] == "ddgksf2013/Rewrite"]
    linked = sync_linked_dependencies(rewrite_entries)
    entries.extend(linked)
    source_rows.append({
        "id":"muyu-linked-dependencies","name":"墨鱼配置引用的 GitHub/Gist 脚本依赖",
        "repo":"多来源","branch":"引用 URL","commit":None,"file_count":len(linked),
        "tree_truncated":False,"errors":[]
    })
    # Stable order keeps manifest diffs readable.
    entries.sort(key=lambda x:(x["repo"],x["path"]))
    manifest = {
        "generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources":source_rows,
        "files":entries,
        "errors":[f"{s['name']}: {e}" for s in source_rows for e in s.get("errors",[])],
        "status_note":"上游已同步不代表 PopCat 兼容；未经转换和设备实测的文件仅作为原始归档。"
    }
    MANIFEST.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n","utf-8")
    print(f"[DONE] {len(entries)} files, {len(manifest['errors'])} source errors")

if __name__ == "__main__":
    main()
