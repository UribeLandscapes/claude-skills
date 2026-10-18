#!/usr/bin/env python3
"""
devibe_scan.py - scan a web codebase for "vibe-coded" / AI-generated design tells.

Plain Python, standard library only. Detection patterns and their severity come from a
Reddit analysis (~3.2M posts / 3,033 on-topic comments across 47 subreddits) of what
people actually flag as making a site look AI-generated. Severity follows how often each
tell is named in that data, so the report tells you where to spend effort.

Usage:
    python3 devibe_scan.py <path>                 # scan a dir or file
    python3 devibe_scan.py <path> --severity high # only high-signal tells
    python3 devibe_scan.py <path> --json          # machine-readable (for CI)
    python3 devibe_scan.py <path> --max 8         # cap examples shown per rule

Exit code is 1 when any HIGH-severity finding exists, 0 otherwise; counts are in the report / JSON.
"""
import os, re, sys, json, argparse

EXTS = {".html", ".htm", ".css", ".scss", ".sass", ".less", ".js", ".jsx",
        ".ts", ".tsx", ".vue", ".svelte", ".astro", ".mdx"}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "out", "vendor",
             "coverage", ".svelte-kit", ".astro", ".turbo", ".cache", "__pycache__"}
W = {"high": 3, "medium": 2, "low": 1}

# Each rule: id, label, severity, fix, and patterns (compiled case-insensitive).
# Keep patterns specific enough to avoid drowning the report in false positives.
RULES = [
    # ---- HIGH: the top concrete tells ----
    {"id": "shadcn-default-card", "label": "Untouched shadcn default Card / theme", "sev": "high",
     "fix": "Theme the tokens (primary, radius, neutrals, spacing). Stock defaults are the giveaway, not shadcn.",
     "pats": [r"rounded-lg\s+border\s+bg-card\s+text-card-foreground\s+shadow-sm",
              r"\"baseColor\"\s*:\s*\"(slate|zinc|gray|neutral|stone)\"",
              r"--radius\s*:\s*0\.5rem"]},
    {"id": "ai-purple", "label": "AI purple / indigo / violet as primary color", "sev": "high",
     "fix": "Pick a brand color outside the violet/indigo/purple band. It is Tailwind's default, so it reads as 'nobody chose this'.",
     "pats": [r"\b(bg|text|from|via|to|border|ring|fill|stroke|decoration|outline)-(indigo|violet|purple|fuchsia)-(400|500|600|700|800)\b",
              r"#(6366f1|4f46e5|818cf8|7c3aed|6d28d9|8b5cf6|a855f7|9333ea|7e22ce|c026d3|d946ef)\b"]},
    {"id": "gradient-text", "label": "Gradient-filled text (heading/hero)", "sev": "high",
     "fix": "Solid color on headings and copy. Gradient body text is one of the strongest AI tells.",
     "pats": [r"bg-clip-text\s+[^\"'`]*text-transparent", r"text-transparent\s+[^\"'`]*bg-clip-text",
              r"-webkit-background-clip\s*:\s*text", r"\bbackground-clip\s*:\s*text"]},
    {"id": "purple-blue-gradient", "label": "Purple-to-blue/pink gradient", "sev": "high",
     "fix": "Default to solid fills. If you must gradient, keep stops analogous and low-contrast, never the rainbow purple-to-blue.",
     "pats": [r"from-(purple|violet|indigo|fuchsia)-\d+\s+(via-[a-z]+-\d+\s+)?to-(blue|indigo|pink|cyan|sky)-\d+",
              r"linear-gradient\([^)]*#(6366f1|7c3aed|8b5cf6|a855f7)[^)]*\)"]},
    {"id": "claude-default-look", "label": "The 'tasteful default' look (cream background + serif display)", "sev": "high",
     "fix": "This is the 2026 tell, not the fix. Anchor color and type to the real brand or a reference. If cream + serif is a genuine decision, mark the line unslop-ignore.",
     "pats": [r"#(faf8f5|f5f1e8|f3eee3|fdfbf7|f7f3ec|faf6ef|f6f1e7|fbf7f0|f4efe4)\b",
              r"\bbg-(stone|amber|orange)-(50|100)\b",
              r"\b(Instrument\s*Serif|Fraunces|Playfair\s*Display|Cormorant|Spectral|DM\s*Serif)\b"]},

    # ---- MEDIUM ----
    {"id": "hero-three-cards", "label": "Centered hero + three-feature-card grid skeleton", "sev": "medium",
     "fix": "Break the grid. Asymmetric hero with a real screenshot; vary sections instead of stacked 3-up icon cards.",
     "pats": [r"grid-cols-1\s+(sm:grid-cols-2\s+)?md:grid-cols-3"]},
    {"id": "rounded-everything", "label": "Large rounded corners / pill buttons everywhere", "sev": "medium",
     "fix": "Use a small, intentional radius scale by role. Not everything maximally rounded; pills only occasionally.",
     "pats": [r"\brounded-(2xl|3xl|full)\b", r"border-radius\s*:\s*(999\d*px|9999px)"],
     # rounded-full on a small sized box is a status dot / avatar / icon, not a pill button. Skip those.
     "suppress": r"\b[hw]-(\d|10|11|12|14|16)(\.5)?\b"},
    {"id": "fade-in-animations", "label": "Boilerplate fade-in / hover-grow / scroll animation", "sev": "medium",
     "fix": "Motion only when it communicates something; gate behind prefers-reduced-motion. Minor tell, noisier signal.",
     "pats": [r"initial=\{\{\s*opacity:\s*0", r"whileInView", r"whileHover=\{\{\s*scale",
              r"data-aos\s*=", r"\bhover:scale-1\d{2}\b"]},
    # Ideas adapted from pbakaus/impeccable, Apache-2.0 (extensions below).
    {"id": "neon-glow", "label": "Unprompted neon glow shadow", "sev": "medium",
     "fix": "Remove glow you did not deliberately design. Dark mode should rely on contrast, not glow.",
     # shortcut: radial fades are candidates; render to verify darkness and visible glow.
     "pats": [r"shadow-\[0_0_", r"drop-shadow-\[0_0_", r"text-shadow\s*:[^;]*\d+px[^;]*(rgba|#|hsl)",
              r"box-shadow\s*:[^;]*\b0\s+0\s+\d{2,}px",
              r"drop-shadow\(\s*0(?:px)?\s+0(?:px)?\s+\d{2,}px"]},
    {"id": "radial-halo", "label": "Neon radial-gradient halo", "sev": "low",
     "fix": "Drop decorative glow blobs unless deliberately designed; verify the rendered result.",
     # shortcut: same-line neon color names/hex only; upgrade if real false negatives appear.
     "pats": [r"radial-gradient\([^;\n]*?(?:cyan|magenta|fuchsia|lime|#0ff\b|#f0f\b|#00ffff|#ff00ff)[^;\n]*?\btransparent\b"]},
    {"id": "emoji-as-icons", "label": "Emoji used as icons / section bullets", "sev": "medium",
     "fix": "Use a real SVG icon set (Lucide/Phosphor/Heroicons) or none. Emoji-as-UI signals low effort.",
     "pats": [r"[\U0001F680✨⚡\U0001F525\U0001F4A1\U0001F512✅\U0001F3AF\U0001F31F\U0001F6E1\U0001F4C8\U0001F511\U0001F389\U0001F680]"]},
    {"id": "generic-font", "label": "Generic default font (Inter / Geist / Roboto / system)", "sev": "medium",
     "fix": "Choose a typeface with character and pair a display + body face. The starter font reads as 'no choice made'.",
     "pats": [r"font-family\s*:\s*['\"]?(Inter|Geist|Roboto|Poppins|Montserrat|Open\s+Sans|Lato|Nunito)\b",
              r"\b(Inter|Geist|Geist_Mono|Roboto|Poppins|Montserrat|Open_Sans|Lato|Nunito)\s*\(",
              r"fontFamily\s*:\s*\{[^}]*['\"](Inter|Geist|Roboto|Poppins|Montserrat|Open\s+Sans|Lato|Nunito)"]},

    # ---- LOW: copy + minor ----
    {"id": "hype-copy", "label": "Generated marketing copy cliche", "sev": "low",
     "fix": "Say what the product literally does, with specifics. Cut the template hype words.",
     "pats": [r"\bTransform your\b", r"\bSupercharge\b", r"\bUnleash\b", r"\bEffortlessly\b",
              r"\breimagined\b", r"take your [^.]{0,30}to the next level", r"\bGame-?changer\b", r"\b(?:revolutionary)\b", r"\bseamless(?:ly)?\s+integrat", r"\bcutting-edge\s+(?:technology|tech|ai|solutions?|platform)\b",
              r"\b(?:innovation|productivity|performance)\s+theater\b"]},
    {"id": "stock-illustration", "label": "Generic blob / stock illustration source", "sev": "low",
     "fix": "Use real screenshots or commissioned art instead of undraw-style blobs.",
     "pats": [r"undraw", r"storyset", r"\bdrawkit\b"]},

    # Ideas adapted from pbakaus/impeccable, Apache-2.0.
    # shortcut: composite checks use SAME-LINE context, not element/declaration-block scope.
    # Multiline CSS can evade them; unrelated elements on one line can match (low severity).
    # Upgrade to element/declaration-block parsing when false-negative reports arrive.
    {"id": "side-tab", "label": "Thick accent edge on a rounded surface", "sev": "low",
     "fix": "Check whether the card needs a colored stripe; let its content establish hierarchy.",
     "pats": [r"^(?=.*\brounded-(?:sm|md|lg|xl|[23]xl)\b)(?=.*\bborder-[ltrb]-(?:[2-9]|[1-9]\d+)\b)(?=.*\bborder-(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}\b)",
              r"^(?=.*\bborder-radius\s*:\s*[1-9]\d*(?:\.\d+)?px)(?=.*\bborder-(?:left|right|top|bottom)\s*:\s*(?:[2-9]|[1-9]\d+)(?:\.\d+)?px\s+solid\s+(?:#[0-9a-f]{3,8}\b|[a-z]+\b))"],
     "suppress": r"\bblockquote\b"},
    {"id": "bounce-easing", "label": "Bouncing or overshooting motion", "sev": "medium",
     "fix": "Use a settled transition unless spring motion supports the interaction; respect reduced motion.",
     "pats": [r"\banimate-bounce\b",
              r"cubic-bezier\(\s*[0-9.]+\s*,\s*(?:1\.\d*[1-9]\d*|[2-9]\d*(?:\.\d+)?|1\d+(?:\.\d+)?)\s*,",
              r"cubic-bezier\(\s*[0-9.]+\s*,\s*-?[0-9.]+\s*,\s*[0-9.]+\s*,\s*(?:1\.\d*[1-9]\d*|[2-9]\d*(?:\.\d+)?|1\d+(?:\.\d+)?)\s*\)"]},
    {"id": "pulsing-dot", "label": "Animated status dot", "sev": "low",
     "fix": "Keep status still unless the pulse conveys a live change the user needs to notice.",
     "pats": [r"^(?=.*\banimate-(?:ping|pulse)\b)(?=.*\brounded-full\b)(?=.*\bh-[1-4](?:\.5)?\b)(?=.*\bw-[1-4](?:\.5)?\b)"]},
    {"id": "marquee", "label": "Continuously scrolling content", "sev": "low",
     "fix": "Let people read a stable list; justify continuous scrolling and provide a pause control.",
     "pats": [r"<marquee\b", r"\banimation(?:-name)?\s*:[^;{}]*\bmarquee\b", r"\banimate-marquee\b"]},
    {"id": "grid-line-background", "label": "Decorative gradient grid", "sev": "low",
     "fix": "Keep the grid only when it belongs to the subject, such as a map or measuring canvas.",
     "pats": [r"^(?=.*\bbackground(?:-image)?\s*:[^;]*linear-gradient\([^;]*linear-gradient\()(?=.*\bbackground-size\s*:)"]},
    {"id": "repeating-stripes-gradient", "label": "Repeating background stripes", "sev": "low",
     "fix": "Use stripes for meaningful texture or data, with a reason tied to this page.",
     "pats": [r"\bbackground(?:-image)?\s*:[^;]*\brepeating-linear-gradient\("]},
    {"id": "organic-clip-path", "label": "Curved silhouette made with CSS clipping", "sev": "low",
     "fix": "Check whether the shape supports the content; use a real cutout for a subject silhouette.",
     # shortcut: only curved path()/shape() syntax; polygon blobs need geometry parsing if requested.
     "pats": [r"\bclip-path\s*:\s*path\([^;]*[cqsa]\s*-?\d",
              r"\bclip-path\s*:\s*shape\([^;]*\bcurve\b"]},
    {"id": "kicker-eyebrow", "label": "Small, widely tracked uppercase label", "sev": "low",
     "fix": "Check if it sits above a heading; remove it if it merely repeats the heading's job.",
     "pats": [r"^(?=.*\buppercase\b)(?=.*\btracking-(?:wide|wider|widest)\b)(?=.*\btext-(?:xs|\[(?:[1-9]|1[01])px\]))",
              r"^(?=.*\btext-transform\s*:\s*uppercase)(?=.*\bletter-spacing\s*:\s*(?:0?\.[1-9]\d*em|[2-9]px))(?=.*\bfont-size\s*:\s*(?:[1-9]|1[0-2])px)"]},
    {"id": "extreme-negative-tracking", "label": "Tracking tighter than -0.04em", "sev": "low",
     "fix": "Loosen tracking to at least -0.04em and inspect the actual heading at each breakpoint.",
     "pats": [r"(?:\bletter-spacing\s*:\s*|\btracking-\[)-(?:0?\.(?:04\d*[1-9]\d*|0[5-9]\d*|[1-9]\d*)|[1-9]\d*(?:\.\d+)?)em\b"]},
    {"id": "thin-border-wide-shadow", "label": "Thin border plus large shadow", "sev": "low",
     "fix": "Choose how this surface shows depth; check whether both the border and wide shadow help.",
     "pats": [r"^(?=.*(?:^|[\s\"'])(?:border|border-\[1px\])(?=[\s\"']))(?=.*\bshadow-(?:xl|2xl)\b)",
              r"^(?=.*\bborder\s*:\s*1px\s+solid)(?=.*\bbox-shadow\s*:\s*-?\d+px\s+-?\d+px\s+(?:[2-9]\d|[1-9]\d{2,})px)"]},
    {"id": "justified-text", "label": "Justified text spacing", "sev": "low",
     "fix": "Use start alignment unless justified copy has been checked for gaps at every width.",
     "pats": [r"\btext-align\s*:\s*justify\b", r"\btext-justify\b"]},
    {"id": "tiny-text", "label": "Text smaller than 12px", "sev": "low",
     "fix": "Increase small labels where possible; check readability at the real rendered size.",
     "pats": [r"(?:\bfont-size\s*:\s*|\btext-\[)(?:0?\.\d*[1-9]\d*|[1-9](?:\.\d+)?|1[01](?:\.\d+)?)px\b"]},
    {"id": "numbered-section-labels", "label": "Decorative two-digit section marker", "sev": "low",
     "fix": "Keep numbers when sequence helps the reader, rather than as filler above each section.",
     "pats": [r"<(?:span|p|div)\b[^>]*\bclass(?:Name)?=[\"'][^\"']*\bsection-(?:number|label)[^\"']*[\"'][^>]*>\s*0[1-9]\s*</",
              r"(?=[^\n]*(?:text-xs|text-sm|tracking-))[^\n]*>\s*0[1-9]\s*(?:/|<)"]},
    {"id": "blinking-cursor", "label": "Decorative blinking cursor", "sev": "low",
     "fix": "Remove the simulated caret unless typing is an actual part of the interaction.",
     "pats": [r"\banimation(?:-name)?\s*:[^;{}]*\b(?:cursor-blink|blink-cursor|caret-blink|blink-caret)\b",
              r"\banimate-(?:cursor-blink|blink-cursor)\b"]},
    {"id": "hard-offset-shadow", "label": "Hard offset block shadow", "sev": "low",
     "fix": "Keep a hard shadow only if the project deliberately uses that visual language.",
     "pats": [r"\bbox-shadow\s*:\s*-?[1-9]\d*px\s+-?[1-9]\d*px\s+0(?:px)?(?=[\s;])"]},
]

def compile_rules(min_sev):
    order = ["high", "medium", "low"]
    floor = order.index(min_sev) if min_sev else len(order) - 1
    out = []
    for r in RULES:
        if order.index(r["sev"]) > floor:
            continue
        r = dict(r)
        r["rx"] = [re.compile(p, re.IGNORECASE) for p in r["pats"]]
        r["suppress_rx"] = re.compile(r["suppress"], re.IGNORECASE) if r.get("suppress") else None
        out.append(r)
    return out

def iter_files(path):
    if os.path.isfile(path):
        yield path; return
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".min.js") or f.endswith(".min.css"):
                continue
            if f == "components.json" or os.path.splitext(f)[1].lower() in EXTS:
                yield os.path.join(root, f)

def scan(path, min_sev):
    rules = compile_rules(min_sev)
    findings = []
    for fp in iter_files(path):
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        if len(lines) == 1 and len(lines[0]) > 5000:   # likely minified
            continue
        for i, line in enumerate(lines, 1):
            if "unslop-ignore" in line.lower():     # respect intentional choices
                continue
            for r in rules:
                if r["suppress_rx"] and r["suppress_rx"].search(line):
                    continue
                for rx in r["rx"]:
                    m = rx.search(line)
                    if m:
                        findings.append({"rule": r["id"], "label": r["label"], "sev": r["sev"],
                                         "fix": r["fix"], "file": fp, "line": i,
                                         "snippet": line.strip()[:160]})
                        break
    return findings

def finding_exit_code(by_sev):
    return int(by_sev.get("high", 0) > 0)

def verdict(by_sev, weighted):
    if by_sev.get("high", 0) >= 3 or weighted >= 15:
        return "STRONG AI-default look"
    if by_sev.get("high", 0) >= 1 or weighted >= 6:
        return "Some AI defaults present"
    if weighted > 0:
        return "Mostly clean, minor tells"
    return "Clean, no tells detected"

def main():
    ap = argparse.ArgumentParser(description="Scan a web codebase for vibe-coded design tells.")
    ap.add_argument("path")
    ap.add_argument("--severity", choices=["high", "medium", "low"], default="low",
                    help="minimum severity to report (default: low = everything)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--max", type=int, default=10, help="max examples shown per rule (text mode)")
    args = ap.parse_args()

    if not os.path.exists(args.path):
        print(f"path not found: {args.path}", file=sys.stderr); sys.exit(2)

    findings = scan(args.path, args.severity)
    by_sev = {}
    by_rule = {}
    for f in findings:
        by_sev[f["sev"]] = by_sev.get(f["sev"], 0) + 1
        by_rule.setdefault(f["rule"], []).append(f)
    weighted = sum(W[s] * n for s, n in by_sev.items())
    files_scanned = sum(1 for _ in iter_files(args.path))

    if args.json:
        print(json.dumps({"path": args.path, "files_scanned": files_scanned,
                          "counts": by_sev, "vibe_score": weighted,
                          "verdict": verdict(by_sev, weighted), "findings": findings}, indent=2))
        sys.exit(finding_exit_code(by_sev))

    sev_order = {"high": 0, "medium": 1, "low": 2}
    rule_ids = sorted(by_rule, key=lambda rid: (sev_order[by_rule[rid][0]["sev"]], -len(by_rule[rid])))
    print(f"\n  unslop-ui scan: {args.path}")
    print(f"  files scanned: {files_scanned}   findings: {len(findings)}   vibe score: {weighted}")
    print(f"  verdict: {verdict(by_sev, weighted)}")
    print(f"  high: {by_sev.get('high',0)}   medium: {by_sev.get('medium',0)}   low: {by_sev.get('low',0)}\n")
    if not findings:
        print("  Nothing flagged. Either it is clean or the tells are layout/motion ones a regex"
              "\n  cannot see. Eyeball the hero layout and animations against references/tells.md.\n")
        return
    for rid in rule_ids:
        items = by_rule[rid]
        f0 = items[0]
        tag = f0["sev"].upper()
        print(f"  [{tag}] {f0['label']}  ({len(items)} hit{'s' if len(items)!=1 else ''})")
        print(f"        fix: {f0['fix']}")
        for it in items[:args.max]:
            print(f"        {it['file']}:{it['line']}  {it['snippet']}")
        if len(items) > args.max:
            print(f"        ... +{len(items) - args.max} more")
        print()
    top = [by_rule[rid][0]['label'] for rid in rule_ids[:3]]
    print("  Top things to change: " + "; ".join(top))
    print("  Layout and motion tells need eyes too. See references/tells.md.\n")
    sys.exit(finding_exit_code(by_sev))

if __name__ == "__main__":
    main()
