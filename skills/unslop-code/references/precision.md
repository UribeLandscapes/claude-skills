# Precision: what the scanner gets right and wrong

The scanner is a lead generator, not a judge. This page shows how it did on real human code
and what we changed, so you know how much to trust a finding.

## Test bed

google/adk-python `src/` (902 files, human-written). Anything the scanner calls "AI tells" here
is mostly a false alarm, so it is a good stress test.

## Before and after

| | Before | After (default view) |
|---|---|---|
| Files scanned | 902 | 844 (58 minified JS bundles skipped) |
| high | 169 | 0 |
| medium | 309 | 116 |
| low | 455 | 17 |
| info (hidden) | n/a | 461 |
| Verdict | STRONG AI-written-code tells | Mostly clean, minor tells |
| Exit code | 1 | 0 |

Per rule (severity in brackets):

| Rule | Before | After |
|---|---|---|
| chat-artifact (high) | 162 | 0 |
| placeholder-comment (high) | 7 | 0 |
| swallowed-errors (medium) | 81 (29 in minified JS) | 27 medium + 24 info, none in JS |
| emoji-in-code (medium) | 42 | 42 |
| narrating-comment (medium) | 184 | 47 |
| narrating-comment-weak (info) | n/a | 130 |
| note-comment (info) | n/a | 24 |
| generic-naming (medium) | 2 | 0 |
| verbose-naming (low to info) | 438 | 283 |
| boilerplate-marker (low) | 17 | 17 |

What moved: 137 of the 162 chat artifacts were ``` fences inside Python docstrings (checked
with the parser); the other 25 were mostly `# Note:` comments (24), now info. All 7 placeholder hits were
ordinary `# TODO: Add <specific thing>` notes; only a bare stub (`# TODO: implement`) counts now.

## Hand check of the 52 Python swallowed handlers

Old scanner: about 43 intentional, about 9 real silent drops. The new scanner reports 51 of the
same 52 (the AST list and the old regex list match by file and line except one: a regex hit
inside an f-string template at `tools/skill_toolset.py:1363`, which is text, not code).

24 are now info (explicit `pylint: disable=broad-except`, a comment of 4+ words, or a
cleanup-only try body, including wrapped calls like `run_in_executor(None, x.close)`, lambda
arguments, and `if`-guarded closes). 27 stay medium. My read of those 27 (file:line):

Look like real silent drops (worth a log line): `cli/agent_test_runner.py:570`,
`cli/utils/graph_serialization.py:159, 189, 222, 279, 287`, and, less sure,
`integrations/firestore/firestore_session_service.py:478` and `workflow/_function_node.py:495`.
That is about 8, close to the old count of 9.

Look intentional but still medium (about 19): telemetry enrichment, fall-through fallbacks and
cleanup written in a shape the rule does not recognise (`getattr(...)` lookups before the close):
`code_executors/container_code_executor.py:120`, `plugins/bigquery_agent_analytics_plugin.py`
lines 427, 537, 2127, 2142, 2158, 5285, 6454, 6857, 7351, 7370, 7377, 7386, 7400, 7408, 7414,
7489, 8982, and `tools/mcp_tool/session_context.py:159`.

The remaining medium group mixes real silent drops with best-effort telemetry, and no syntax
separates them, so they are leads for a human read, not verdicts.

## Method

1. Run the old scanner on real human code and hand-check a sample of each loud rule.
2. Write the suspicious rule twice, once as a regex and once on the AST, and compare the two
   lists by file and line, not by totals. Equal totals can hide different lines. Here the lists
   differ by exactly one line, and it is the regex being wrong.
3. Demote what is mostly human to `info` instead of deleting it, so it stays reachable with
   `--severity info`.
4. Keep a synthetic sloppy file (top-level ``` fence, `# ... rest of your code`,
   `except Exception: pass`, `def process_data`). It must still be STRONG.

## Known limits

- Only Python gets string and AST awareness. In JS/TS/etc. a ``` inside a template literal is
  still flagged high, and minified files are skipped only by name or by line shape.
- A long excuse comment (4+ words) demotes a swallowed handler to info, so an AI-written
  `# ignore errors because it is fine` slips down. `--severity info` shows it with its reason.
- A bare `except:` is demoted only by an explicit code (`noqa: E722`, `pylint: disable=bare-except`).
- Cleanup detection looks at the call name and its arguments (including lambdas) and `if`
  bodies; a close preceded by a lookup or assignment in the same try is not recognised.
- A file that does not parse (for example a pasted ``` fence) falls back to the regex rules.
- Verdict is density based (weighted score per file): a few medium hits in a big tree read as
  "mostly clean" (3 or more high hits are always STRONG). Read the per-rule list, not just the verdict.
