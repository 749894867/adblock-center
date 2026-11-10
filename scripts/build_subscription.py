#!/usr/bin/env python3
"""Build one stable, remotely subscribable Surge-style ad-block module.

Only syntax that can be safely represented as a rule is emitted. Quantumult X
JSON/script response rewrites are intentionally not copied into the output.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
SOURCE_ROOT = SITE / "sources"
OUTPUT = SITE / "downloads" / "AdBlock-Center.sgmodule"
MANIFEST = SITE / "data" / "manifest.json"

# Conservative common-denominator rule types. IP ranges and compound AND/OR
# expressions are deliberately excluded to avoid broad collateral blocking.
DOMAIN_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}
RULE_TYPES = DOMAIN_TYPES  # PopCat external RULE-SET lists do not support URL-REGEX
BLOCK_ACTIONS = {"REJECT", "REJECT-DROP", "REJECT-TINYGIF", "REJECT-NO-DROP"}
SOURCE_EXTENSIONS = {".conf", ".sgmodule", ".snippet", ".list", ".txt"}


def normalize_rule(line: str) -> str | None:
    """Return a normalized blocking rule, or None for unsupported/non-block rules."""
    value = line.strip().rstrip("\ufeff")
    if not value or value.startswith(("#", ";", "//")) or value.startswith("["):
        return None

    # Surge/Shadowrocket-style list entries, with or without an explicit policy.
    first, sep, rest = value.partition(",")
    rule_type = first.strip().upper()
    if not sep or rule_type not in RULE_TYPES:
        return None

    if rule_type in DOMAIN_TYPES:
        fields = [part.strip() for part in value.split(",")]
        if len(fields) == 2 and fields[1]:
            return f"{rule_type},{fields[1].lower()},REJECT"
        if len(fields) >= 3 and fields[1] and fields[2].upper() in BLOCK_ACTIONS:
            return f"{rule_type},{fields[1].lower()},REJECT"
        return None

    # URL-REGEX can contain commas, so only inspect a terminal policy marker.
    payload = rest.strip()
    if not payload:
        return None
    policy_markers = BLOCK_ACTIONS | {"DIRECT", "PROXY", "PASS", "COMPATIBLE", "DEFAULT"}
    if "," in payload:
        possible_policy = payload.rsplit(",", 1)[-1].strip().upper()
        if possible_policy in policy_markers:
            if possible_policy not in BLOCK_ACTIONS:
                return None
            payload = payload.rsplit(",", 1)[0].rstrip()
    if not payload:
        return None
    return f"URL-REGEX,{payload},REJECT"


def convert_qx_simple_block(line: str) -> str | None:
    """Convert only simple QX host/url rejection lines to equivalent rules."""
    value = line.strip()
    host_match = re.match(
        r"^(host|host-suffix)\s*,\s*([^,\s]+)\s*,\s*reject\s*$",
        value,
        re.IGNORECASE,
    )
    if host_match:
        kind = "DOMAIN" if host_match.group(1).lower() == "host" else "DOMAIN-SUFFIX"
        return f"{kind},{host_match.group(2)},REJECT"

    # PopCat's standalone external RULE-SET format does not support URL-REGEX.
    # Keep QX URL/path rejection rules in the source archive until they can be
    # converted into a compatible [URL Rewrite] module section.
    return None


def iter_rule_lines(path: Path):
    """Yield lines in [Rule] sections, or all lines for plain rule-list files."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return

    has_sections = any(re.match(r"^\s*\[[^\]]+\]\s*$", line) for line in text.splitlines())
    in_rule_section = not has_sections
    for line in text.splitlines():
        section = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if section:
            in_rule_section = section.group(1).strip().lower() == "rule"
            continue

        # QX simple host/url rejection directives usually live outside sections.
        converted = convert_qx_simple_block(line)
        if converted:
            yield converted
            continue

        if in_rule_section:
            normalized = normalize_rule(line)
            if normalized:
                yield normalized



def build_bilibili_scripts() -> list[dict[str, object]]:
    """Publish the upstream app2smile Bilibili scripts on our stable Pages host.

    Prefer the freshly synchronized upstream copies. Keep repository-local
    vendor copies as a fail-safe when GitHub temporarily cannot serve a source.
    """
    upstream_root = SOURCE_ROOT / "app2smile" / "js"
    vendor_root = ROOT / "scripts" / "vendor"
    output_root = SITE / "scripts"
    output_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    for filename in ("bilibili-json.js", "bilibili-proto.js"):
        upstream = upstream_root / filename
        vendor = vendor_root / filename
        source = upstream if upstream.is_file() and upstream.stat().st_size > 0 else vendor
        if not source.is_file():
            raise SystemExit(f"Missing Bilibili script and fallback: {filename}")

        content = source.read_text(encoding="utf-8-sig")
        if not content.strip():
            raise SystemExit(f"Bilibili script is empty: {filename}")

        # The upstream JSON script calls $done() but forgets to return when a
        # response has no body. Continuing then throws after $done(), which
        # invalidates the response in PopCat. Stop cleanly after the pass-through.
        if filename == "bilibili-json.js":
            needle = "    $done({});\n}\nif (method !== \"GET\")"
            replacement = "    $done({});\n    return;\n}\nif (method !== \"GET\")"
            if needle in content:
                content = content.replace(needle, replacement, 1)

        if "$done(" not in content:
            raise SystemExit(f"Bilibili script has no $done() callback: {filename}")
        if filename == "bilibili-proto.js" and "pako.ungzip" not in content:
            raise SystemExit("Bilibili protobuf script is missing its gzip decoder")

        encoded = content.encode("utf-8")
        if len(encoded) > 2 * 1024 * 1024:
            raise SystemExit(f"Bilibili script exceeds the 2 MiB safe size: {filename}")

        target = output_root / filename
        target.write_bytes(encoded)
        rows.append({
            "path": f"scripts/{filename}",
            "url": f"https://749894867.github.io/adblock-center/scripts/{filename}",
            "source": "app2smile/rules",
            "source_path": f"js/{filename}",
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "size_bytes": len(encoded),
        })

    return rows

def main() -> None:
    if not SOURCE_ROOT.exists():
        raise SystemExit("site/sources is missing; run scripts/sync_sources.py first")

    bilibili_scripts = build_bilibili_scripts()

    rules: set[str] = set()
    scanned_files = 0
    files_with_rules = 0
    per_type: Counter[str] = Counter()
    per_source: Counter[str] = Counter()

    for path in sorted(SOURCE_ROOT.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        scanned_files += 1
        before = len(rules)
        source_name = path.relative_to(SOURCE_ROOT).parts[0]
        for rule in iter_rule_lines(path):
            if rule not in rules:
                rules.add(rule)
                per_type[rule.split(",", 1)[0]] += 1
                per_source[source_name] += 1
        if len(rules) > before:
            files_with_rules += 1

    if not rules:
        raise SystemExit("No compatible blocking rules were found; refusing to publish an empty subscription")

    # Compact equivalent domain-suffix rules. DOMAIN-SUFFIX,example.com also
    # covers every subdomain, so a narrower suffix such as api.example.com is
    # redundant whenever the broader suffix is already present.
    suffixes = {
        rule.split(",", 2)[1].strip().lower().rstrip(".")
        for rule in rules
        if rule.startswith("DOMAIN-SUFFIX,")
    }
    compact_suffixes: set[str] = set()
    redundant_suffixes = 0
    for domain in sorted(suffixes, key=lambda d: (d.count("."), len(d), d)):
        labels = domain.split(".")
        covered = any(".".join(labels[i:]) in suffixes for i in range(1, len(labels)))
        if covered:
            redundant_suffixes += 1
        else:
            compact_suffixes.add(domain)

    compact_rules: set[str] = set()
    redundant_exact_domains = 0
    for rule in rules:
        kind, value, action = rule.split(",", 2)
        if kind == "DOMAIN-SUFFIX":
            value = value.strip().lower().rstrip(".")
            if value not in compact_suffixes:
                continue
            compact_rules.add(f"DOMAIN-SUFFIX,{value},{action}")
        elif kind == "DOMAIN":
            domain = value.strip().lower().rstrip(".")
            labels = domain.split(".")
            covered = any(".".join(labels[i:]) in compact_suffixes for i in range(len(labels)))
            if covered:
                redundant_exact_domains += 1
                continue
            compact_rules.add(f"DOMAIN,{domain},{action}")
        else:
            compact_rules.add(rule)

    rules = compact_rules
    per_type = Counter(rule.split(",", 1)[0] for rule in rules)
    ordered_types = ["DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "URL-REGEX"]
    ordered_rules = sorted(
        rules,
        key=lambda rule: (
            ordered_types.index(rule.split(",", 1)[0]),
            rule.casefold(),
        ),
    )

    # Publish large rule sets as small remote shards. The user imports only the
    # stable .sgmodule once; RULE-SET downloads each plain-text shard separately.
    # Keep remote downloads small for mobile clients such as PopCat.
    # PopCat downloads and stores external RULE-SET files when the module updates.
    shard_max_bytes = 500_000
    shard_payloads: list[list[str]] = []
    current: list[str] = []
    current_bytes = 0
    for rule in ordered_rules:
        if not rule.endswith(",REJECT"):
            raise SystemExit(f"Unexpected non-REJECT action in generated rule: {rule[:120]}")
        rule_without_policy = rule[:-len(",REJECT")]
        line_bytes = len((rule_without_policy + "\n").encode("utf-8"))
        if current and current_bytes + line_bytes > shard_max_bytes:
            shard_payloads.append(current)
            current = []
            current_bytes = 0
        if line_bytes > shard_max_bytes:
            raise SystemExit("A single generated rule exceeds shard size limit")
        current.append(rule_without_policy)
        current_bytes += line_bytes
    if current:
        shard_payloads.append(current)

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rules_dir = SITE / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    for old_shard in rules_dir.glob("AdBlock-*.list"):
        old_shard.unlink()

    shard_rows = []
    for index, chunk in enumerate(shard_payloads, start=1):
        filename = f"AdBlock-{index:02d}.list"
        shard_text = (
            "# Auto-generated by AdBlock Center. Do not edit manually.\n"
            + "\n".join(chunk)
            + "\n"
        )
        shard_bytes = len(shard_text.encode("utf-8"))
        if shard_bytes > 3_000_000:
            raise SystemExit(f"{filename} exceeds the safe shard response size: {shard_bytes} bytes")
        shard_path = rules_dir / filename
        shard_path.write_text(shard_text, encoding="utf-8")
        shard_rows.append({
            "path": f"rules/{filename}",
            "url": f"https://749894867.github.io/adblock-center/rules/{filename}",
            "rule_count": len(chunk),
            "size_bytes": shard_bytes,
        })

    # Publish a single plain-text RULE-SET URL for clients that do not expand
    # external RULE-SET references inside module metadata. One URL is added once
    # in the client's rule editor; the file is refreshed by the normal daily build.
    # Validate against PopCat's documented external-list grammar.
    supported_external_types = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}
    for rule in ordered_rules:
        fields = rule.split(",")
        if len(fields) != 3 or fields[0] not in supported_external_types or fields[2] != "REJECT":
            raise SystemExit(f"Rule is not valid in a PopCat RULE-SET: {rule[:160]}")

    single_list_text = (
        "# Auto-generated by AdBlock Center. Do not edit manually.\n"
        + "\n".join(rule[:-len(",REJECT")] for rule in ordered_rules)
        + "\n"
    )
    single_list_bytes = len(single_list_text.encode("utf-8"))
    single_list_lines = single_list_text.splitlines()
    if len(single_list_lines) != len(ordered_rules) + 1:
        raise SystemExit("Single rule list must contain exactly one line per rule")
    single_list_limit = 8_000_000  # stay below the client's 8 MiB response-body cap
    if single_list_bytes > single_list_limit:
        raise SystemExit(
            f"Single remote rule set exceeds safe 8 MB limit: {single_list_bytes} bytes; "
            "reduce/compact sources before publishing."
        )
    single_list_path = rules_dir / "AdBlock-Center.list"
    single_list_path.write_text(single_list_text, encoding="utf-8")

    module_lines = [
        "#!name=墨鱼广告净化 · 海外代理模式",
        "#!desc=海外代理模式下生效；含通用广告规则与 Bilibili 响应净化。Bilibili 需要开启 HTTPS 解密并信任证书。",
        "#!category=AdBlock",
        "#",
        f"# Generated: {generated_at}",
        f"# Rules: {len(ordered_rules)}",
        f"# Remote rule shards: {len(shard_rows)}",
        "# External lists contain PopCat-supported domain rule types only.",
        "# Update this module in PopCat to refresh remote rule lists and Bilibili scripts.",
        "",
        "[Rule]",
    ]
    for shard in shard_rows:
        module_lines.append(f"RULE-SET,{shard['url']},REJECT")

    # These Bilibili-only URL rules are evaluated on decrypted requests.
    # app.bilibili.com and grpc.biliapi.net are handled by the response scripts below.
    module_lines.extend([
        "DOMAIN-SUFFIX,cm.bilibili.com,REJECT",
        "URL-REGEX,^https?:\\\\/\\\\/api\\\\.bilibili\\\\.com\\\\/x\\\\/v2\\\\/dm\\\\/ad,REJECT",
        "",
        "[MITM]",
        "hostname = %APPEND% app.bilibili.com, grpc.biliapi.net, api.bilibili.com",
        "",
        "[Script]",
        f"bili_json = type=http-response,pattern=^https:\\\\/\\\\/app\\\\.bilibili\\\\.com\\\\/x\\\\/(v2\\\\/(splash\\\\/(list|show)|feed\\\\/index\\\\?)|resource\\\\/show\\\\/tab\\\\/v2),requires-body=1,timeout=25,script-path={bilibili_scripts[0]['url']}",
        f"bili_proto = type=http-response,pattern=^https:\\\\/\\\\/(grpc\\\\.biliapi\\\\.net|app\\\\.bilibili\\\\.com)\\\\/bilibili\\\\.app\\\\.(viewunite\\\\.v1\\\\.View\\\\/View|dynamic\\\\.v2\\\\.Dynamic\\\\/DynAll)$,requires-body=1,binary-body-mode=1,timeout=30,script-path={bilibili_scripts[1]['url']}",
    ])
    module_text = "\n".join(module_lines) + "\n"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(module_text, encoding="utf-8")
    output_bytes = len(module_text.encode("utf-8"))

    manifest = {}
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    manifest["single_rule_set"] = {
        "path": "rules/AdBlock-Center.list",
        "url": "https://749894867.github.io/adblock-center/rules/AdBlock-Center.list",
        "generated_at": generated_at,
        "rule_count": len(ordered_rules),
        "size_bytes": single_list_bytes,
        "max_safe_bytes": single_list_limit,
        "format": "Surge/Shadowrocket RULE-SET text; policy is selected in the client",
    }
    manifest["bilibili"] = {
        "supported": True,
        "requires_https_decryption": True,
        "mitm_hosts": ["app.bilibili.com", "grpc.biliapi.net", "api.bilibili.com"],
        "scripts": bilibili_scripts,
        "coverage": ["开屏页", "推荐页", "底部 Tab", "视频详情页", "动态/UP 主推荐", "弹幕广告接口"],
        "note": "基于 app2smile/rules 的 Surge 模块与脚本适配 PopCat；需要用户安装并完全信任 HTTPS 解密证书。",
    }
    manifest["subscription"] = {
        "path": "downloads/AdBlock-Center.sgmodule",
        "url": "https://749894867.github.io/adblock-center/downloads/AdBlock-Center.sgmodule",
        "generated_at": generated_at,
        "rule_count": len(ordered_rules),
        "size_bytes": output_bytes,
        "shard_count": len(shard_rows),
        "largest_shard_bytes": max((row["size_bytes"] for row in shard_rows), default=0),
        "shards": shard_rows,
        "redundant_suffix_rules_removed": redundant_suffixes,
        "redundant_exact_domain_rules_removed": redundant_exact_domains,
        "source_files_scanned": scanned_files,
        "files_contributing_rules": files_with_rules,
        "rule_types": dict(per_type),
        "source_rule_counts": dict(per_source),
        "compatibility_note": "Stable compact module references external Surge-style RULE-SET shards. QX script-response-body and jsonjq-response-body rewrites are not included.",
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[BUILD] {OUTPUT.relative_to(ROOT)} ({output_bytes} bytes)")
    print(f"[BUILD] {len(ordered_rules)} unique rules in {len(shard_rows)} shards")
    print(f"[BILIBILI] Published {len(bilibili_scripts)} scripts: " + ", ".join(x["path"] for x in bilibili_scripts))
    for shard in shard_rows:
        print(f"[SHARD] {shard['path']}: {shard['rule_count']} rules, {shard['size_bytes']} bytes")
    print("[BUILD] by type: " + json.dumps(dict(per_type), ensure_ascii=False, sort_keys=True))
    print("[BUILD] by source: " + json.dumps(dict(per_source), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
