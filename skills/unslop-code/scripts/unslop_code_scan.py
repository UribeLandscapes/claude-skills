#!/usr/bin/env python3
"""
unslop_code_scan.py - scan source code for the mechanical "tells" that give away
AI-written code, across languages.

Plain Python, standard library only. The patterns and their severity come from a Reddit
analysis (11,906 on-topic posts + 11,306 comments across 55 AI / coding / SaaS subreddits,
2020 to 2026) of what developers actually name as a giveaway that code was written by an
LLM. Every candidate was classified by an LLM into a fixed taxonomy of 19 tells, then each
top tell was adversarially verified by re-reading the quotes. Severity follows the verified
share, so the report tells you where to spend effort.

What it can and cannot see. The loudest tells in the data are about shape and substance:
boilerplate / tutorial-shaped code (verified 18.6%, the #1 tell), hallucinated APIs (11.2%),
over-engineering (7.8%), ignoring the surrounding codebase (3.5%), mixed skill level (1.9%).
A regex cannot judge any of those; they are documented in references/tells.md for a human or
a compiler. This scanner catches the mechanical, surface tells: leftover chat/assistant text,
placeholder comments, emoji, swallowed errors, narrating comments, and generic placeholder
names.

Two axes, not one. Severity is how loudly a finding reads as an AI tell (how conclusive it is
when present). Class is whether it is a bug or a cosmetic. They are independent: a swallowed
error is a quiet tell but a real bug; an emoji is a loud tell but harmless. Findings are leads,
not verdicts: read each bug-class one and fix the ones that really hide a failure; treat the
cosmetic ones as the lighter pass.

Precision. Severity `info` (weight 0, not in score, verdict or exit code) holds weak signals and
is hidden unless you pass --severity info. Python files are parsed: text inside a multi-line
string (docstring examples) is not flagged, and swallowed handlers are judged on the AST. See
references/precision.md for measured before/after numbers and the known limits.

The highest-impact bug, hallucinated APIs, is invisible to this scanner. Catch it the way only
code allows: build it, type-check it, run it, resolve every import and call against real docs.
The scanner is the cheap second pass, never the first.

Language coverage. Some tells are language-universal because they live in comments or strings:
emoji, leftover chat artifacts, placeholder/ellipsis comments, narrating comments. Others key
on syntax and are language-specific. Swallowed errors are matched for Python / JS / TS / Java /
C# / Ruby try-catch and for Go's single-line empty `if err != nil {}`; Rust's `.unwrap()` /
`let _ =` and Go's `_`-discard are too overloaded to flag by regex without noise, so they are a
human pass (see references/tells.md). Generic-name detection keys on def / function / func /
fn / fun / sub.

Usage:
    python3 unslop_code_scan.py <path>                 # scan a dir or file
    python3 unslop_code_scan.py <path> --severity high # only the strongest signals
    python3 unslop_code_scan.py <path> --severity info # also show the weak (info) signals
    python3 unslop_code_scan.py <path> --json          # machine-readable (for CI)
    python3 unslop_code_scan.py <path> --max 8         # cap examples shown per rule

A line containing  unslop-ignore  is skipped, for a pattern you are using on purpose.
Exit code is 1 when any HIGH-severity finding exists, 0 otherwise; counts are in the report / JSON.
Verdict is density-based (weighted score per file scanned) so a big clean tree is not drowned out.
"""
import os, re, sys, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # find _py_ast under -I / any cwd
import _py_ast

EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".rb", ".php",
        ".c", ".h", ".cpp", ".cc", ".hpp", ".cs", ".kt", ".kts", ".swift", ".scala",
        ".m", ".mm", ".sh", ".bash", ".lua", ".dart", ".vue", ".svelte", ".sql", ".r"}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "out", "vendor", "target",
             "coverage", ".venv", "venv", "__pycache__", ".idea", ".gradle", "bin", "obj"}
W = {"high": 3, "medium": 2, "low": 1, "info": 0}
ORDER = ["high", "medium", "low", "info"]
STRING_KEEP = {"emoji-in-code", "boilerplate-marker"}   # still flagged inside Python strings
MAX_LINE, MEAN_LINE = 1000, 200   # generated/minified file thresholds
CMT = r"(?://|#|/\*|\*|--|<!--)"   # comment openers across common languages
EMOJI = ("\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF"
         "\U00002B00-\U00002BFF\U0001F900-\U0001F9FF\U00002764")

# Each rule: id, label, severity, class, share (the verified data share), fix, patterns.
# Severity follows the verified shares, with the two conclusive paste-in artifacts raised
# to HIGH because when they survive into committed code they are unmistakable.
# Class is the orthogonal axis the goal cares about: "bug" means the code is wrong (it eats a
# failure, ships unfinished, or will not run) and you fix it on those grounds alone; "cosmetic"
# means it is the model's chat voice leaking into the file, a lighter surface pass. Severity and
# class are independent: a swallowed error is MEDIUM severity but a bug; a chat artifact is HIGH
# severity but cosmetic. The biggest bug (hallucinated APIs) is structural and not in this list.
RULES = [
    # ---------- HIGH: the conclusive paste-in artifacts ----------
    {"id": "chat-artifact", "label": "Leftover chat / assistant / markdown artifact in the code", "sev": "high", "class": "cosmetic",
     "share": "verified 1.2%, but unmistakable when present",
     "fix": "Delete the assistant's voice before committing: code fences, 'Here's the updated code', 'As an AI', 'Good catch!', a trailing 'Note:'.",
     "pats": [r"^\s*```", r"\bhere'?s the (updated|complete|full|fixed|revised|new) (code|version|implementation|file)\b",
              r"\bas an? (ai|a\.i\.) (language )?model\b", r"\bas a large language model\b",
              r"\b(good|great) catch!", r"\byou'?re absolutely right\b",
              r"\b(certainly|sure)! here('?s| is)\b", r"\bi'?ll (add|update|fix|implement) .{0,40}\bnow\b",
              r"\bi hope this helps\b", r"\blet me know if you'?d?\s*(like|need|want)\b"]},
    {"id": "placeholder-comment", "label": "Placeholder / ellipsis comment left in (\"// rest of your code\")", "sev": "high", "class": "bug",
     "share": "verified 1.6%, precision ~100%",
     "fix": "Write the actual code. These '... rest of the code' stubs mean the file is unfinished, not just untidy.",
     "pats": [r"" + CMT + r"\s*\.{2,}\s*(rest|the rest|your|remaining|existing|previous|other)\b",
              r"" + CMT + r"\s*(rest|remainder) of (your |the |my )?(code|implementation|logic|function|file|owl)\b",
              r"" + CMT + r"\s*(your|the) (code|logic|implementation|stuff) (goes )?here\b",
              r"" + CMT + r"\s*(add|insert|implement|put) (your )?(code|logic|implementation) here\b",
              r"" + CMT + r"\s*(implementation|code|logic) (goes |go )?here\b",
              r"" + CMT + r"\s*existing code (here|unchanged|stays|remains)\b",
              r"" + CMT + r"\s*\.\.\. ?\((?:rest|your|the|existing)[^)]*\)",
              r"" + CMT + r"\s*TODO:?\s*(implement|add|fill in|finish)"
              r"(\s+(this|here|me|it|the rest|the logic|the implementation|the code))?\s*[.!]?\s*(\*/|-->)?\s*$"]},

    # ---------- MEDIUM: the surface tells ----------
    {"id": "emoji-in-code", "label": "Emoji in code, comments, strings, logs, or commit text", "sev": "medium", "class": "cosmetic",
     "share": "verified 3.9%, precision ~77% (the highest-precision cosmetic tell)",
     "fix": "Remove emoji from source. They survive from the model's chat output and read as vibe-coded.",
     "pats": [r"[" + EMOJI + r"]"]},
    {"id": "swallowed-errors", "label": "Catch-all / swallowed errors (bare except, empty catch, empty Go err block)", "sev": "medium", "class": "bug",
     "share": "verified 3.1% (try/except wrapped around everything)",
     "fix": "Catch specific exceptions and handle them. A bare except, an empty catch, or an empty `if err != nil {}` eats the one clue you needed.",
     "pats": [r"^\s*except\s*:", r"^\s*except\s+(Exception|BaseException)(?:\s+as\s+\w+)?\s*:",
              r"\bcatch\s*\([^)]*\)\s*\{\s*\}", r"\bcatch\s*\{\s*\}",
              r"\brescue\s*=>\s*\w+\s*$", r"\bcatch\s*\([^)]*\)\s*\{\s*//",
              r"\bif\s+err\s*!=\s*nil\s*\{\s*\}", r"\bif\s+err\s*!=\s*nil\s*\{\s*//"]},
    {"id": "narrating-comment", "label": "Narrating comment that restates the obvious / step-by-step '# Step 1'", "sev": "medium", "class": "cosmetic",
     "share": "verified 8.5% (over-commenting; the regex catches the obvious-restatement subset)",
     "fix": "Cut comments that say what the next line plainly does. Comment why, not what.",
     "pats": [r"" + CMT + r"\s*(step\s*\d+\b|now we\b|first,|next,|then,|finally,)",
              r"" + CMT + r"\s*this (function|method|line|loop|variable|class|block) (does|is|will|handles|returns|creates)\b",
              r"" + CMT + r"\s*(import|importing) (the |required )?(libraries|modules|dependencies)\b"]},
    {"id": "generic-naming", "label": "Generic placeholder function name (process_data, handleData, doStuff)", "sev": "medium", "class": "cosmetic",
     "share": "verified 1.9%, precision ~100% (process_data() is the canonical example)",
     "fix": "Name it for what it actually does in this domain. process_data() that does 11 things is the AI tell.",
     "pats": [r"\b(def|function|func|fn|fun|sub)\s+(process_?[Dd]ata|handle_?[Dd]ata|do_?[Ss]tuff|do_?[Ss]omething|my_?[Ff]unction|process_?[Ii]tem|process_?[Ii]nput|main_?[Ff]unction)\b",
              r"\b(process_?[Dd]ata|handle_?[Dd]ata|do_?[Ss]tuff|do_?[Ss]omething)\s*\("]},

    # ---------- LOW: weak / inflated signals ----------
    {"id": "narrating-comment-weak", "label": "Comment opening with a verb (# Create the client); common in human code", "sev": "info", "class": "cosmetic",
     "share": "weak: measured mostly human on adk-python; kept as a lead only",
     "fix": "Cut it only if it restates the next line; verb-first comments are often fine.",
     "pats": [r"" + CMT + r"\s*(increment|decrement|initialize|declare|define|create|instantiate|loop (over|through)|iterate over|return the|set the|get the|assign|call the)\b"]},
    {"id": "note-comment", "label": "'# Note:' / 'Remember:' / 'Important:' comment", "sev": "info", "class": "cosmetic",
     "share": "weak: ordinary in human code (24 of 24 on adk-python were human)",
     "fix": "Fine when it carries a real caveat; delete it when it only chats.",
     "pats": [r"^\s*" + CMT + r"\s*(note|remember|important|keep in mind|tip)\s*:"]},
    {"id": "verbose-naming", "label": "Over-verbose, robotically self-documenting identifier", "sev": "info", "class": "cosmetic",
     "share": "verified 0.4% (inflated; people mostly argue FOR descriptive names)", "cs": True,
     "fix": "A name that is a whole sentence (getUserDataFromApiResponseHandler) reads as machine-generated. Trim it.",
     "pats": [r"\b[a-z]+([A-Z][a-z0-9]+){4,}\b", r"\b[a-z]+(_[a-z0-9]+){5,}\b"]},
    {"id": "boilerplate-marker", "label": "Boilerplate / dummy-data marker (placeholder keys, sample data, 'Successfully' logs)", "sev": "low", "class": "cosmetic",
     "share": "weak proxy for the #1 tell (tutorial-shaped code), which is otherwise not regex-detectable",
     "fix": "Replace generated dummy data and placeholders with the real thing. Tutorial-shaped sample data is the loudest tell, but only a human can judge the shape.",
     "pats": [r"\blorem ipsum\b", r"\b(your|my)[-_]?api[-_]?key\b", r"\bYOUR_API_KEY\b", r"\bexample\.com\b",
              r"\b(John|Jane) (Doe|Smith)\b", r"['\"]sk-(xxx|your|placeholder|123)",
              r"(console\.log|print|println|fmt\.Print\w*|System\.out\.print\w*)\s*\(\s*['\"](✅|🚀|Successfully|Done!|Here we go)"]},
]

def compile_rules():
    out = []
    for r in RULES:
        r = dict(r)
        flags = 0 if r.get("cs") else re.IGNORECASE
        r["rx"] = [re.compile(p, flags) for p in r["pats"]]
        out.append(r)
    return out

def iter_files(path):
    if os.path.isfile(path):
        yield path; return
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".map"):
                continue
            if os.path.splitext(f)[1].lower() in EXTS:
                yield os.path.join(root, f)

def empty_exception_handler(lines, index):
    line = lines[index]
    match = re.match(
        r"^([ \t]*)except\s+(Exception|BaseException)(?:\s+as\s+\w+)?\s*:(.*)$",
        line,
    )
    if not match:
        return True  # Other swallowed-error patterns keep their existing behavior.
    inline = match.group(3).split("#", 1)[0].strip()
    if inline:
        return inline in {"pass", "..."}
    indent = len(match.group(1).expandtabs())
    for following in lines[index + 1:]:
        body = following.split("#", 1)[0].strip()
        if not body:
            continue
        prefix = following[:len(following) - len(following.lstrip())]
        if len(prefix.expandtabs()) <= indent:
            break
        if body not in {"pass", "..."}:
            return False
    return True

def is_generated(fp, lines):
    """Minified / bundled output: `.min.` in the name, or very long lines on average."""
    if ".min." in os.path.basename(fp).lower():
        return True
    if not lines:
        return False
    return max(len(l) for l in lines) > MAX_LINE and sum(len(l) for l in lines) / len(lines) > MEAN_LINE

def make(r, fp, line_no, match, line, sev=None, reason=None):
    f = {"rule": r["id"], "label": r["label"], "sev": sev or r["sev"], "class": r["class"],
         "share": r["share"], "fix": r["fix"], "file": fp, "line": line_no,
         "match": match.strip()[:50], "snippet": line.strip()[:160]}
    if reason:
        f["reason"] = reason
    return f

def first_hit(r, line, line_no, spans):
    """First regex match of rule r on the line that is not inside a Python string literal."""
    for rx in r["rx"]:
        for m in rx.finditer(line):
            start = m.start() + len(m.group(0)) - len(m.group(0).lstrip())
            if spans and r["id"] not in STRING_KEEP and _py_ast.in_string(spans, line_no, line, start):
                continue
            return m
    return None

def scan_file(fp, lines, rules):
    # shortcut: non-Python files have no string/AST awareness; a ``` inside a JS template
    # literal is still flagged. Upgrade trigger: a stdlib-only JS tokenizer, or a real parser.
    tree = _py_ast.parse(lines) if fp.endswith(".py") else None
    spans = _py_ast.string_spans(tree) if tree else []
    by_id = {r["id"]: r for r in rules}
    out = []
    for i, line in enumerate(lines, 1):
        if "unslop-ignore" in line.lower():
            continue
        for r in rules:
            if tree and r["id"] == "swallowed-errors":
                continue
            m = first_hit(r, line, i, spans)
            if m and not (r["id"] == "swallowed-errors" and not empty_exception_handler(lines, i - 1)):
                out.append(make(r, fp, i, m.group(0), line))
    if tree:
        for ln, sev, why in _py_ast.swallowed(tree, lines):
            if "unslop-ignore" not in lines[ln - 1].lower():
                out.append(make(by_id["swallowed-errors"], fp, ln, lines[ln - 1].split(":")[0], lines[ln - 1], sev, why))
    return out

def scan(path):
    """-> (all findings incl. info, files_scanned, files_skipped_generated)."""
    rules = compile_rules()
    findings, scanned, skipped = [], 0, 0
    for fp in iter_files(path):
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except Exception:
            continue
        if is_generated(fp, lines):
            skipped += 1
            continue
        scanned += 1
        findings.extend(scan_file(fp, lines, rules))
    return sorted(findings, key=lambda f: (f["file"], f["line"])), scanned, skipped

def finding_exit_code(by_sev):
    return int(by_sev.get("high", 0) > 0)

def verdict(by_sev, density):
    high = by_sev.get("high", 0)
    if high >= 3 or density >= 1.0:
        return "STRONG AI-written-code tells"
    if high >= 1 or density >= 0.3:
        return "Some AI tells present"
    if density > 0:
        return "Mostly clean, minor tells"
    return "Clean, no surface tells detected"

def print_report(args, findings, by_sev, by_class, weighted, density, scanned, skipped, hidden):
    groups = {}
    for f in findings:
        groups.setdefault((f["rule"], f["sev"]), []).append(f)
    keys = sorted(groups, key=lambda k: (ORDER.index(k[1]), -len(groups[k])))
    print(f"\n  unslop-code scan: {args.path}")
    print(f"  files scanned: {scanned}   skipped (generated/minified): {skipped}   findings: {len(findings)}   slop score: {weighted}   density: {density:.2f}")
    print(f"  verdict: {verdict(by_sev, density)}")
    print(f"  high: {by_sev.get('high',0)}   medium: {by_sev.get('medium',0)}   low: {by_sev.get('low',0)}   info: {by_sev.get('info',0)}")
    if hidden:
        print(f"  {hidden} info finding{'s' if hidden != 1 else ''} hidden (weak signals; --severity info to see them)")
    print(f"  bug-class: {by_class.get('bug',0)} (code that may hide a failure; read each, fix the real ones)   "
          f"cosmetic: {by_class.get('cosmetic',0)} (surface giveaways)\n")
    if not findings:
        print("  No surface tells flagged. The loudest tells are structural and a regex cannot see\n"
              "  them: boilerplate / tutorial-shaped code, hallucinated APIs, over-engineering, and\n"
              "  code that ignores the surrounding repo. Hallucinated APIs are a bug, not a style\n"
              "  issue: build it, type-check it, run it, resolve every import. Then read the diff for\n"
              "  the rest by hand against references/tells.md.\n")
        return
    for k in keys:
        items = groups[k]
        f0 = items[0]
        print(f"  [{f0['sev'].upper()} · {f0['class']}] {f0['label']}  ({len(items)} hit{'s' if len(items)!=1 else ''})  [{f0['share']}]")
        print(f"        fix: {f0['fix']}")
        for it in items[:args.max]:
            why = f"  [{it['reason']}]" if it.get("reason") else ""
            print(f"        {it['file']}:{it['line']}  ({it['match']})  {it['snippet']}{why}")
        if len(items) > args.max:
            print(f"        ... +{len(items) - args.max} more")
        print()
    print("  Top things to change: " + "; ".join(groups[k][0]['label'] for k in keys[:3]))
    print("  Findings are leads, not verdicts. Read the bug-class ones first and fix the ones that really hide a failure.")
    print("  The big tells (boilerplate, hallucinated APIs, over-engineering) the regex cannot see:")
    print("  build / type-check / run for the hallucinated calls, read the diff for the rest. See references/tells.md.\n")

def main():
    ap = argparse.ArgumentParser(description="Scan source code for AI-written-code tells.")
    ap.add_argument("path")
    ap.add_argument("--severity", choices=ORDER, default="low",
                    help="minimum severity to report (default: low; 'info' also shows weak signals)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--max", type=int, default=10, help="max examples shown per rule (text mode)")
    args = ap.parse_args()

    if not os.path.exists(args.path):
        print(f"path not found: {args.path}", file=sys.stderr); sys.exit(2)

    all_findings, scanned, skipped = scan(args.path)
    floor = ORDER.index(args.severity)
    findings = [f for f in all_findings if ORDER.index(f["sev"]) <= floor]
    hidden = sum(1 for f in all_findings if f["sev"] == "info") if floor < ORDER.index("info") else 0
    by_sev, by_class = {}, {}
    for f in findings:
        by_sev[f["sev"]] = by_sev.get(f["sev"], 0) + 1
        by_class[f["class"]] = by_class.get(f["class"], 0) + 1
    weighted = sum(W[s] * n for s, n in by_sev.items())
    density = weighted / max(scanned, 1)

    if args.json:
        print(json.dumps({"path": args.path, "files_scanned": scanned, "files_skipped_generated": skipped,
                          "counts": by_sev, "class_counts": by_class, "slop_score": weighted,
                          "density": round(density, 4), "info_hidden": hidden,
                          "verdict": verdict(by_sev, density), "findings": findings}, indent=2))
        sys.exit(finding_exit_code(by_sev))
    print_report(args, findings, by_sev, by_class, weighted, density, scanned, skipped, hidden)
    sys.exit(finding_exit_code(by_sev))

if __name__ == "__main__":
    main()
