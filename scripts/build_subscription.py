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

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    header = [
        "#!name=墨鱼广告净化 · 每日自动更新",
        "#!desc=固定 HTTPS 订阅地址；上游规则每日同步并重建。仅包含可转换的域名/URL 拦截规则。",
        "#!category=AdBlock",
        "#",
        f"# Generated: {generated_at}",
        f"# Rules: {len(ordered_rules)}",
        f"# Source files scanned: {scanned_files}; files contributing rules: {files_with_rules}",
        "# Only block actions are included. Unsupported QX response-body scripts/JSON rewrites are excluded.",
        "",
        "[Rule]",
        *ordered_rules,
        "",
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temp_output = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    output_text = "\n".join(header)
    output_bytes = len(output_text.encode("utf-8"))
    if output_bytes > 7_800_000:
        raise SystemExit(
            f"Subscription remains too large ({output_bytes} bytes); "
            "will not publish a file that exceeds PopCat's 8 MiB response limit."
        )
    temp_output.write_text(output_text, encoding="utf-8")
    temp_output.replace(OUTPUT)

    manifest = {}
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    manifest["subscription"] = {
        "path": "downloads/AdBlock-Center.sgmodule",
        "url": "https://749894867.github.io/adblock-center/downloads/AdBlock-Center.sgmodule",
        "generated_at": generated_at,
        "rule_count": len(ordered_rules),
        "size_bytes": output_bytes,
        "redundant_suffix_rules_removed": redundant_suffixes,
        "redundant_exact_domain_rules_removed": redundant_exact_domains,
        "source_files_scanned": scanned_files,
        "files_contributing_rules": files_with_rules,
        "rule_types": dict(per_type),
        "source_rule_counts": dict(per_source),
        "compatibility_note": "Conservative Surge-style rules only. QX script-response-body and jsonjq-response-body rewrites are not included.",
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[BUILD] {OUTPUT.relative_to(ROOT)}")
    print(f"[BUILD] {len(ordered_rules)} unique rules from {files_with_rules}/{scanned_files} files")
    print("[BUILD] by type: " + json.dumps(dict(per_type), ensure_ascii=False, sort_keys=True))
    print("[BUILD] by source: " + json.dumps(dict(per_source), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
