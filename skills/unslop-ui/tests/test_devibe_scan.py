import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "devibe_scan.py"


class ScannerTests(unittest.TestCase):
    def run_scan(self, path, json_mode=True, extra_args=()):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(path)] + (["--json"] if json_mode else []) + list(extra_args),
            capture_output=True, text=True,
        )

    def test_high_count_does_not_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.css"
            path.write_text('.item { color: #6366f1; }\n' * 256)
            for json_mode in (True, False):
                with self.subTest(json_mode=json_mode):
                    result = self.run_scan(path, json_mode)
                    self.assertEqual(result.returncode, 1)
                    if json_mode:
                        self.assertEqual(json.loads(result.stdout)["counts"]["high"], 256)
                    else:
                        self.assertIn("high: 256", result.stdout)

    def test_clean_file_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.css"
            path.write_text('.item { color: black; }\n')
            for json_mode in (True, False):
                with self.subTest(json_mode=json_mode):
                    self.assertEqual(self.run_scan(path, json_mode).returncode, 0)

    def test_missing_path_exits_two(self):
        with tempfile.TemporaryDirectory() as directory:
            for json_mode in (True, False):
                with self.subTest(json_mode=json_mode):
                    self.assertEqual(
                        self.run_scan(Path(directory) / "missing", json_mode).returncode, 2
                    )

    def test_directory_scans_components_json_only(self):
        with tempfile.TemporaryDirectory() as directory:
            components = Path(directory) / "components.json"
            components.write_text('{"baseColor": "slate"}')
            (Path(directory) / "other.json").write_text('{"baseColor": "slate"}')
            result = self.run_scan(directory)
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertEqual(report["files_scanned"], 1)
            self.assertEqual(len(report["findings"]), 1)
            self.assertEqual(report["findings"][0]["file"], str(components))
            self.assertEqual(report["findings"][0]["rule"], "shadcn-default-card")

    def rule_ids(self, source, severity="low"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.html"
            path.write_text(source)
            result = self.run_scan(path, extra_args=("--severity", severity))
            self.assertIn(result.returncode, (0, 1), result.stderr)
            return {f["rule"] for f in json.loads(result.stdout)["findings"]}

    def assert_rule(self, rule, positive, negative):
        self.assertIn(rule, self.rule_ids(positive))
        self.assertNotIn(rule, self.rule_ids(negative))

    def test_side_tab(self):
        self.assert_rule("side-tab",
                         '<div class="rounded-xl border-l-4 border-indigo-500 shadow-2xl">',
                         '<blockquote class="rounded-xl border-l-4 border-red-500">')
        ids = self.rule_ids('<div class="rounded-xl border-l-4 border-indigo-500 shadow-2xl">')
        self.assertIn("ai-purple", ids)
        self.assertNotIn("thin-border-wide-shadow", ids)

    def test_bounce_easing(self):
        self.assert_rule("bounce-easing", 'class="animate-bounce"',
                         'transition-timing-function: cubic-bezier(0.2, 1, 0.4, 1);')
        for curve in ("0.2, 1.2, 0.4, 1", "0.2, 0, 0.4, 1.01"):
            self.assertIn("bounce-easing", self.rule_ids(f"easing: cubic-bezier({curve});"))

    def test_pulsing_dot(self):
        self.assert_rule("pulsing-dot", 'class="h-2 w-2 rounded-full animate-ping"',
                         'class="animate-pulse h-24 w-full"')
        self.assertIn("pulsing-dot", self.rule_ids('class="animate-pulse rounded-full h-3 w-3"'))

    def test_marquee(self):
        self.assert_rule("marquee", '<marquee>Partners</marquee>', '<div>Partners</div>')
        self.assertIn("marquee", self.rule_ids('animation: marquee 20s linear infinite;'))

    def test_grid_line_background(self):
        self.assert_rule("grid-line-background",
                         '.grid { background: linear-gradient(red, transparent), linear-gradient(90deg, red, transparent); background-size: 24px 24px; }',
                         '.surface { background: linear-gradient(red, blue); background-size: cover; }')

    def test_repeating_stripes_gradient(self):
        self.assert_rule("repeating-stripes-gradient",
                         'background: repeating-linear-gradient(45deg, red 0 1px, white 1px 8px);',
                         'background: linear-gradient(45deg, red, white);')

    def test_organic_clip_path(self):
        self.assert_rule("organic-clip-path", 'clip-path: shape(from 0% 20%, curve to 100% 80% with 40% 0%);',
                         'clip-path: inset(10px);')

    def test_kicker_eyebrow(self):
        self.assert_rule("kicker-eyebrow", 'class="uppercase tracking-widest text-xs"',
                         'class="uppercase tracking-normal text-base"')

    def test_extreme_negative_tracking(self):
        self.assert_rule("extreme-negative-tracking", 'letter-spacing: -0.041em;',
                         'letter-spacing: -0.04em;')
        for value in ('-0.05em', '-.1em', '-1em', '-.04001em'):
            self.assertIn("extreme-negative-tracking", self.rule_ids(f'class="tracking-[{value}]"'))
        self.assertNotIn("extreme-negative-tracking", self.rule_ids('letter-spacing: -0.039em;'))

    def test_thin_border_wide_shadow(self):
        self.assert_rule("thin-border-wide-shadow", 'class="border border-gray-200 shadow-2xl"',
                         'class="border-4 border-gray-200 shadow-2xl"')

    def test_justified_text(self):
        self.assert_rule("justified-text", 'text-align: justify;', 'text-align: left;')
        self.assertIn("justified-text", self.rule_ids('class="text-justify"'))

    def test_tiny_text(self):
        self.assert_rule("tiny-text", 'font-size: 11.5px;', 'font-size: 12px;')
        for size in ("9px", "0.5px", ".5px"):
            self.assertIn("tiny-text", self.rule_ids(f'class="text-[{size}]"'))
        self.assertNotIn("tiny-text", self.rule_ids('font-size: 0px;'))

    def test_numbered_section_labels(self):
        self.assert_rule("numbered-section-labels", '<span class="section-number">01</span>',
                         '<span class="count">101</span>')
        self.assert_rule("numbered-section-labels", '<span class="text-xs tracking-widest">01 /</span>',
                         '<td>05</td>')

    def test_blinking_cursor(self):
        self.assert_rule("blinking-cursor", 'animation: cursor-blink 1s steps(2) infinite;',
                         'cursor: text;')

    def test_hard_offset_shadow(self):
        self.assert_rule("hard-offset-shadow", 'box-shadow: 4px 4px 0 #222;',
                         'box-shadow: 4px 4px 12px #222;')

    def test_extended_neon_glow(self):
        self.assert_rule("neon-glow", 'filter: drop-shadow(0 0 24px cyan);',
                         'background: radial-gradient(circle, white, gray);')

    def test_radial_halo(self):
        for source in ('background: radial-gradient(circle, #00ffff 0%, transparent 70%);',
                       'class="bg-[radial-gradient(circle,theme(colors.fuchsia.500),transparent)]"'):
            self.assert_rule("radial-halo", source, 'background: radial-gradient(circle, white, gray);')
        dark = 'background: radial-gradient(circle, rgba(0,0,0,.4), transparent);'
        self.assertNotIn("radial-halo", self.rule_ids(dark))
        self.assertNotIn("neon-glow", self.rule_ids(dark))
        split = 'background: radial-gradient(circle, white, gray); color: cyan; border-color: transparent;'
        self.assertNotIn("radial-halo", self.rule_ids(split))

    def test_extended_generic_font(self):
        for source in ('font-family: "Poppins", sans-serif;', 'font-family: "Open Sans";',
                       'Montserrat({ subsets: ["latin"] })', 'Open_Sans({})',
                       'fontFamily: { body: ["Nunito"] }', 'fontFamily: { body: ["Open  Sans"] }'):
            self.assert_rule("generic-font", source, 'font-family: "Project Custom";')

    def test_extended_hype_copy(self):
        for source in ('Revolutionary workflow', 'Seamless integration', 'Cutting-edge technology',
                       'Innovation theater', 'Productivity theater', 'Performance theater'):
            self.assert_rule("hype-copy", source, 'Export invoices as CSV.')
        for source in ('const seamless = true', 'a seamless edge', 'the cutting-edge of the table'):
            self.assertNotIn("hype-copy", self.rule_ids(source))

    def test_new_rule_ignore_and_suppress(self):
        self.assertNotIn("justified-text", self.rule_ids('text-align: justify; /* unslop-ignore */'))
        self.assertNotIn("side-tab", self.rule_ids(
            '<blockquote style="border-radius: 12px; border-left: 4px solid red;">'))

    def test_new_rules_are_advisory_and_hidden_at_high(self):
        source = 'class="animate-bounce text-justify"'
        self.assertEqual(self.rule_ids(source, "high"), set())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.html"
            path.write_text(source)
            for json_mode in (True, False):
                result = self.run_scan(path, json_mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                if json_mode:
                    counts = json.loads(result.stdout)["counts"]
                    self.assertGreater(counts.get("medium", 0), 0)
                    self.assertGreater(counts.get("low", 0), 0)
                else:
                    self.assertIn("high: 0", result.stdout)

    def test_composite_class_order_is_irrelevant(self):
        for source, rule in (
            ('class="border-red-500 border-l-4 rounded-xl"', "side-tab"),
            ('class="shadow-xl border-gray-200 border"', "thin-border-wide-shadow"),
            ('class="text-xs tracking-widest uppercase"', "kicker-eyebrow"),
        ):
            self.assertIn(rule, self.rule_ids(source))

    def test_composite_known_line_scope_limits(self):
        # shortcut: line scope can join unrelated elements; parse elements/blocks if reported.
        self.assertIn("thin-border-wide-shadow", self.rule_ids(
            '<div class="border"></div><div class="shadow-xl"></div>'))
        self.assertNotIn("side-tab", self.rule_ids(
            '.card {\n border-radius: 12px;\n border-left: 4px solid red;\n}'))

    def test_css_and_secondary_pattern_forms(self):
        for rule, positive, negative in (
            ("side-tab", '.card { border-left: 4px solid red; border-radius: 12px; }',
             '.card { border-left: 1px solid red; border-radius: 12px; }'),
            ("kicker-eyebrow", 'font-size: 12px; letter-spacing: .2em; text-transform: uppercase;',
             'font-size: 16px; letter-spacing: .2em; text-transform: uppercase;'),
            ("thin-border-wide-shadow", 'box-shadow: 0px 4px 24px #222; border: 1px solid gray;',
             'box-shadow: 0px 4px 8px #222; border: 1px solid gray;'),
            ("organic-clip-path", 'clip-path: path("M 0 0 C 20 40 30 20 0 0");',
             'clip-path: path("M 0 0 L 20 40 L 30 20 Z");'),
            ("blinking-cursor", 'class="animate-blink-cursor"', 'class="cursor-text"'),
            ("marquee", 'class="animate-marquee"', 'class="marquee-heading"'),
        ):
            with self.subTest(rule=rule):
                self.assert_rule(rule, positive, negative)

    def test_all_new_rule_severities(self):
        # Read declarations without importing or running the scanner's command-line entry point.
        import ast
        tree = ast.parse(SCRIPT.read_text())
        rules = next(ast.literal_eval(node.value) for node in tree.body
                     if isinstance(node, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "RULES" for t in node.targets))
        existing = {"shadcn-default-card", "ai-purple", "gradient-text", "purple-blue-gradient",
                    "claude-default-look", "hero-three-cards", "rounded-everything",
                    "fade-in-animations", "neon-glow", "emoji-as-icons", "generic-font",
                    "hype-copy", "stock-illustration"}
        new_rules = [rule for rule in rules if rule["id"] not in existing]
        self.assertEqual(len(new_rules), 16)
        for rule in new_rules:
            with self.subTest(rule=rule["id"]):
                self.assertIn(rule["sev"], ("medium", "low"))

    def test_tracking_boundary_does_not_suppress_other_findings(self):
        self.assertIn("extreme-negative-tracking", self.rule_ids(
            '.a { letter-spacing: -0.04em; } .b { letter-spacing: -0.05em; }'))
        for value in ('-.04em', '-0.0400em', '-0.03999em'):
            self.assertNotIn("extreme-negative-tracking", self.rule_ids(f'letter-spacing: {value};'))
