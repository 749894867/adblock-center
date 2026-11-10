#!/usr/bin/env python3
"""Build one stable, remotely subscribable Surge-style ad-block module.

Only syntax that can be safely represented as a rule is emitted. Quantumult X
JSON/script response rewrites are intentionally not copied into the output.
"""
from __future__ import annotations

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
RULE_TYPES = DOMAIN_TYPES | {"URL-REGEX"}
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
            return f"{rule_type},{fields[1]},REJECT"
        if len(fields) >= 3 and fields[1] and fields[2].upper() in BLOCK_ACTIONS:
            return f"{rule_type},{fields[1]},REJECT"
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

    # QX url-reject rules become URL matching rules. Response-body scripts and
    # JSON rewrites are NOT converted here because they need client-specific code.
    url_match = re.match(r"^(.*?)\s+url\s+reject(?:-200)?\s*$", value, re.IGNORECASE)
    if url_match:
        pattern = url_match.group(1).strip()
        if pattern.startswith("^") and len(pattern) > 4:
            return f"URL-REGEX,{pattern},REJECT"
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


def main() -> None:
    if not SOURCE_ROOT.exists():
        raise SystemExit("site/sources is missing; run scripts/sync_sources.py first")

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
    shard_max_bytes = 2_500_000
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
        "#!name=墨鱼广告净化 · 每日自动更新",
        "#!desc=固定 HTTPS 订阅入口；规则分片每日同步更新，无需重复导入。",
        "#!category=AdBlock",
        "#",
        f"# Generated: {generated_at}",
        f"# Rules: {len(ordered_rules)}",
        f"# Remote rule shards: {len(shard_rows)}",
        "# The module stays small; each referenced RULE-SET is a separate plain-text list.",
        "",
        "[Rule]",
    ]
    for shard in shard_rows:
        module_lines.append(
            f"RULE-SET,{shard['url']},REJECT,update-interval=86400"
        )
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
    for shard in shard_rows:
        print(f"[SHARD] {shard['path']}: {shard['rule_count']} rules, {shard['size_bytes']} bytes")
    print("[BUILD] by type: " + json.dumps(dict(per_type), ensure_ascii=False, sort_keys=True))
    print("[BUILD] by source: " + json.dumps(dict(per_source), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
