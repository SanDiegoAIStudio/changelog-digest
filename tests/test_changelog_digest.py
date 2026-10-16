from __future__ import annotations

import contextlib
import datetime
import io
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import changelog_digest as cd

_ABSENT = object()


def run_main(argv):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        with contextlib.redirect_stderr(stderr):
            code = cd.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def line_startswith(text, prefix):
    return any(line.startswith(prefix) for line in text.splitlines())


def write_text(directory, name, text):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def install_module(testcase, module):
    previous = sys.modules.get("anthropic", _ABSENT)
    sys.modules["anthropic"] = module

    def restore():
        if previous is _ABSENT:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = previous

    testcase.addCleanup(restore)
    return module


def fake_anthropic():
    module = types.ModuleType("anthropic")

    class AnthropicError(Exception):
        pass

    class APIStatusError(AnthropicError):
        def __init__(self, message="", status_code=None):
            super().__init__(message)
            self.message = message
            self.status_code = status_code

    class AuthenticationError(APIStatusError):
        pass

    class NotFoundError(APIStatusError):
        pass

    class RateLimitError(APIStatusError):
        pass

    class APIConnectionError(AnthropicError):
        pass

    class Messages:
        on_create = None

        def create(self, **kwargs):
            if Messages.on_create is None:
                raise AssertionError("messages.create was called")
            return Messages.on_create(**kwargs)

    class Anthropic:
        on_init = None

        def __init__(self):
            if Anthropic.on_init is not None:
                Anthropic.on_init()
            self.messages = Messages()

    module.AnthropicError = AnthropicError
    module.APIStatusError = APIStatusError
    module.AuthenticationError = AuthenticationError
    module.NotFoundError = NotFoundError
    module.RateLimitError = RateLimitError
    module.APIConnectionError = APIConnectionError
    module.Messages = Messages
    module.Anthropic = Anthropic
    return module


def text_response(text, stop_reason="end_turn", blocks=None):
    if blocks is None:
        blocks = [types.SimpleNamespace(type="text", text=text)]
    return types.SimpleNamespace(content=blocks, stop_reason=stop_reason)


def run_with_model(testcase, extra_args):
    module = fake_anthropic()
    install_module(testcase, module)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return text_response("A digest.")

    module.Messages.on_create = create
    with tempfile.TemporaryDirectory() as directory:
        path = write_text(directory, "log.md", entry_log())
        code, stdout, stderr = run_main([path, "--date", "2026-03-14"] + list(extra_args))
    return code, stdout, stderr, calls


def entry_log(day="2026-03-14", time="09:30", project="garden-planner", title="Frost dates", body="Moved the peas."):
    heading = "## {0}".format(day)
    if time:
        heading += " {0}".format(time)
    heading += " - [{0}] {1}".format(project, title)
    return "\n".join(
        [
            heading,
            "",
            "**Commit:** 4f2a9c1",
            "**Session:** Seed calendar",
            "",
            "### What Changed",
            body,
            "",
        ]
    )


def readme_commands(text):
    commands = []
    mode = None
    for line in text.splitlines():
        if mode is None and line.startswith("`" * 3 + "bash"):
            mode = "bash"
            continue
        if mode is None and line.startswith("`" * 3):
            mode = "other"
            continue
        if mode is not None and line.startswith("`" * 3):
            mode = None
            continue
        if mode == "bash" and line.strip():
            commands.append(line.strip())
    return commands


class ChangelogDigestTest(unittest.TestCase):
    def test_01_heading_with_and_without_time(self):
        """source: an entry heading with a time and one without both keep the date, time, project, and title."""
        text = "\n".join(
            [
                "## 2026-03-14 09:30 - [ garden-planner ] Frost dates",
                "## 2026-03-12 - [recipe-box] Soup night",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(result.skipped, ())
        self.assertEqual(len(result.entries), 2)
        first, second = result.entries
        self.assertEqual(first.date, datetime.date(2026, 3, 14))
        self.assertEqual(first.time, "09:30")
        self.assertEqual(first.project, "garden-planner")
        self.assertEqual(first.title, "Frost dates")
        self.assertEqual(first.line, 1)
        self.assertEqual(second.date, datetime.date(2026, 3, 12))
        self.assertEqual(second.time, "")
        self.assertEqual(second.project, "recipe-box")
        self.assertEqual(second.title, "Soup night")
        self.assertEqual(second.line, 2)

    def test_02_fields_sections_and_separator(self):
        """source: inline fields and sections stay in file order, bodies drop only surrounding blank lines, and text after a separator is left out."""
        text = "\n".join(
            [
                "## 2026-03-14 09:30 - [garden-planner] Frost",
                "",
                "**Commit:** 4f2a9c1",
                "**Session:** Seed calendar",
                "**Note:** extra",
                "",
                "### What Changed",
                "",
                "Shifted the dates.",
                "",
                "Kept the old week.",
                "",
                "### Key Features",
                "- **Frost lookup:** one line",
                "",
                "### Empty",
                "",
                "### Inspiration",
                "",
                "A late cold snap.",
                "",
                "#### still body",
                "",
                "---",
                "not attached",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 1)
        entry = result.entries[0]
        self.assertEqual(
            entry.fields,
            (
                ("Commit", "4f2a9c1"),
                ("Session", "Seed calendar"),
                ("Note", "extra"),
            ),
        )
        self.assertEqual(entry.field("commit"), "4f2a9c1")
        self.assertEqual(entry.field("NOTE"), "extra")
        self.assertEqual(entry.field("missing"), "")
        self.assertEqual(
            [label for label, _body in entry.sections],
            ["What Changed", "Key Features", "Empty", "Inspiration"],
        )
        self.assertEqual(
            entry.section("what changed"),
            "Shifted the dates.\n\nKept the old week.",
        )
        self.assertEqual(entry.section("Key Features"), "- **Frost lookup:** one line")
        self.assertEqual(entry.section("Empty"), "")
        self.assertEqual(
            entry.section("Inspiration"),
            "A late cold snap.\n\n#### still body",
        )
        self.assertEqual(entry.section("missing"), "")
        blob = "\n".join(body for _label, body in entry.sections)
        self.assertNotIn("not attached", blob)

    def test_03_non_entry_heading_is_reported(self):
        """source: a "## " heading that is not an entry is reported, never dropped silently."""
        lines = [
            "## 2026-03-14 09:30 - [garden-planner] Real",
            "",
            "### What Changed",
            "Before.",
            "",
            "## Notes from the shed",
            "This must not leak into the entry.",
        ]
        result = cd.parse_changelog("\n".join(lines))
        self.assertEqual(result.skipped, ((6, "## Notes from the shed"),))
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].section("What Changed"), "Before.")
        self.assertNotIn("leak", result.entries[0].section("What Changed"))

    def test_04_impossible_date_and_time_are_skipped(self):
        """source: an impossible date or an impossible time is not parsed as an entry."""
        text = "\n".join(
            [
                "## 2026-02-30 09:30 - [garden-planner] Impossible day",
                "## 2026-03-14 25:00 - [garden-planner] Impossible hour",
                "## 2026-03-14 09:30 - [recipe-box] Real title",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].title, "Real title")
        self.assertEqual(
            result.skipped,
            (
                (1, "## 2026-02-30 09:30 - [garden-planner] Impossible day"),
                (2, "## 2026-03-14 25:00 - [garden-planner] Impossible hour"),
            ),
        )

    def test_05_fenced_block_stays_in_body(self):
        """source: a fenced block keeps an entry-shaped heading, a separator, and a field line inside the section body."""
        text = "\n".join(
            [
                "## 2026-03-14 09:30 - [garden-planner] Real",
                "",
                "**Commit:** abcdef0",
                "",
                "### What Changed",
                "Intro",
                "",
                "`" * 3,
                "**Commit:** ffffffe",
                "## 2026-03-15 10:00 - [recipe-box] Fake",
                "---",
                "`" * 3,
                "After.",
                "",
                "### Inspiration",
                "A note.",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 1)
        entry = result.entries[0]
        self.assertEqual(entry.project, "garden-planner")
        self.assertEqual(entry.fields, (("Commit", "abcdef0"),))
        self.assertEqual(
            entry.section("What Changed"),
            "\n".join(
                [
                    "Intro",
                    "",
                    "`" * 3,
                    "**Commit:** ffffffe",
                    "## 2026-03-15 10:00 - [recipe-box] Fake",
                    "---",
                    "`" * 3,
                    "After.",
                ]
            ),
        )
        self.assertEqual(entry.section("Inspiration"), "A note.")

    def test_06_summary_before_first_section(self):
        """source: free text before the first section is kept as a Summary section."""
        text = "\n".join(
            [
                "## 2026-03-14 - [garden-planner] Notes",
                "",
                "**Session:** Map",
                "",
                "The bed map was redrawn.",
                "",
                "### What Changed",
                "Moved two beds.",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 1)
        entry = result.entries[0]
        self.assertEqual(entry.sections[0][0], "Summary")
        self.assertEqual(entry.section("Summary"), "The bed map was redrawn.")
        self.assertEqual(entry.section("What Changed"), "Moved two beds.")
        self.assertEqual(entry.field("Session"), "Map")

    def test_07_entries_for_date_sorts_untimed_first(self):
        """source: selecting one day returns only that date, with an entry that has no time before timed entries."""
        day = datetime.date(2026, 3, 14)
        other = datetime.date(2026, 3, 12)
        entries = (
            cd.Entry(day, "16:10", "recipe-box", "Late", (), (), 5),
            cd.Entry(other, "09:30", "garden-planner", "Other day", (), (), 2),
            cd.Entry(day, "", "garden-planner", "Untimed later line", (), (), 10),
            cd.Entry(day, "09:30", "garden-planner", "Morning", (), (), 1),
            cd.Entry(day, "", "garden-planner", "Untimed earlier line", (), (), 2),
        )
        chosen = cd.entries_for_date(entries, day)
        self.assertIsInstance(chosen, list)
        self.assertEqual(
            [entry.title for entry in chosen],
            [
                "Untimed earlier line",
                "Untimed later line",
                "Morning",
                "Late",
            ],
        )
        self.assertEqual(entries[1].title, "Other day")

    def test_08_entries_for_week_bounds(self):
        """source: a week includes the end date and the day six days earlier, and excludes the days just outside."""
        end = datetime.date(2026, 3, 14)
        entries = [
            cd.Entry(end + datetime.timedelta(days=1), "09:00", "recipe-box", "After", (), (), 1),
            cd.Entry(end - datetime.timedelta(days=7), "", "recipe-box", "Before", (), (), 2),
            cd.Entry(end - datetime.timedelta(days=6), "10:00", "garden-planner", "Start timed", (), (), 3),
            cd.Entry(end - datetime.timedelta(days=6), "", "garden-planner", "Start untimed", (), (), 8),
            cd.Entry(end - datetime.timedelta(days=3), "12:00", "recipe-box", "Middle", (), (), 4),
            cd.Entry(end, "09:00", "garden-planner", "End", (), (), 1),
        ]
        chosen = cd.entries_for_week(entries, end)
        self.assertEqual(
            [entry.title for entry in chosen],
            ["Start untimed", "Start timed", "Middle", "End"],
        )

    def test_09_daily_prompt_omits_files_modified(self):
        """source: a daily prompt includes the What Changed body and leaves out the Files Modified body."""
        entry = cd.Entry(
            date=datetime.date(2026, 3, 14),
            time="09:30",
            project="garden-planner",
            title="Frost",
            fields=(
                ("Commit", "4f2a9c1"),
                ("Session", "Seed calendar"),
                ("Mood", "calm"),
            ),
            sections=(
                ("What Changed", "UNIQUE_CHANGED"),
                ("Files Modified", "UNIQUE_FILES"),
                ("Inspiration", "A cold snap."),
            ),
            line=1,
        )
        prompt = cd.build_daily_prompt([entry], datetime.date(2026, 3, 14))
        self.assertIn("UNIQUE_CHANGED", prompt)
        self.assertNotIn("UNIQUE_FILES", prompt)
        self.assertNotIn("Files Modified", prompt)
        self.assertIn("**Mood:** calm", prompt)
        self.assertIn("**Time:** 09:30", prompt)
        self.assertFalse(prompt.endswith("\n"))

    def test_10_weekly_prompt_groups_projects(self):
        """source: a weekly prompt lists projects in first-appearance order, and each entry line is "- [date] session: title"."""
        entries = [
            cd.Entry(
                date=datetime.date(2026, 3, 10),
                time="16:10",
                project="recipe-box",
                title="Timers keep the oven honest",
                fields=(("Session", "Oven timer"),),
                sections=(
                    ("What Changed", "Paused on\n\nlid open."),
                    ("Breakthroughs", "A short   bump\tis ignored."),
                    ("Files Modified", "src/timer.py"),
                ),
                line=4,
            ),
            cd.Entry(
                date=datetime.date(2026, 3, 12),
                time="",
                project="garden-planner",
                title="Soil notes",
                fields=(),
                sections=(("What Changed", "Notes on each bed."),),
                line=10,
            ),
            cd.Entry(
                date=datetime.date(2026, 3, 14),
                time="09:30",
                project="recipe-box",
                title="A second pass",
                fields=(("Session", ""), ("Commit", "aaaaaaa")),
                sections=(),
                line=12,
            ),
        ]
        prompt = cd.build_weekly_prompt(entries, datetime.date(2026, 3, 14))
        block = "\n".join(
            [
                "## recipe-box",
                "- [2026-03-10] Oven timer: Timers keep the oven honest",
                "  - What changed: Paused on lid open.",
                "  - Breakthrough: A short bump is ignored.",
                "- [2026-03-14] Unnamed session: A second pass",
                "",
                "## garden-planner",
                "- [2026-03-12] Unnamed session: Soil notes",
                "  - What changed: Notes on each bed.",
            ]
        )
        self.assertIn(block, prompt)
        self.assertLess(prompt.index("## recipe-box"), prompt.index("## garden-planner"))
        self.assertNotIn("src/timer.py", prompt)
        self.assertFalse(prompt.endswith("\n"))
        self.assertTrue(prompt.endswith("Generate the digest in Markdown format."))

    def test_11_sample_dry_run_is_byte_stable(self):
        """source: rule, every command the README documents must run, and the sample run is byte-stable.

        Dry runs go through main. A real run is executed with the model package absent, so it stops before any network call. The unittest command is checked as text and not launched.
        """
        readme_path = os.path.join(ROOT, "README.md")
        with open(readme_path, encoding="utf-8") as handle:
            commands = readme_commands(handle.read())
        self.assertIn("python3 -m unittest discover -s tests -v", commands)
        self.assertNotIn("uv sync", commands)
        sample_argv = [
            "examples/sample-changelog.md",
            "--date",
            "2026-03-14",
            "--dry-run",
        ]
        prompt_path = os.path.join(ROOT, "examples", "sample-prompt.txt")
        with open(prompt_path, encoding="utf-8") as handle:
            expected = handle.read()
        sample_text_path = os.path.join(ROOT, "examples", "sample-changelog.md")
        with open(sample_text_path, encoding="utf-8") as handle:
            parsed = cd.parse_changelog(handle.read())
        self.assertEqual(len(parsed.entries), 3)
        self.assertEqual(parsed.skipped, ())
        self.assertNotIn("src/calendar.py", expected)
        self.assertNotIn("src/timer.py", expected)
        self.assertNotIn("2026-03-12", expected)
        self.assertIn("The countdown now pauses", expected)

        install_module(self, None)
        previous = os.getcwd()
        os.chdir(ROOT)
        try:
            for _repeat in range(2):
                code, stdout, stderr = run_main(sample_argv)
                self.assertEqual(code, 0)
                self.assertEqual(stderr, "")
                self.assertEqual(stdout, expected)
            for command in commands:
                if command == "python3 -m unittest discover -s tests -v":
                    continue
                python3_dry_run = command.startswith(
                    "python3 changelog_digest.py "
                ) and "--dry-run" in command
                self.assertTrue(
                    command.startswith("uv run changelog_digest.py ") or python3_dry_run,
                    command,
                )
                self.assertIn("--date 2026-03-14", command)
                parts = command.split()
                if python3_dry_run:
                    self.assertEqual(parts[:2], ["python3", "changelog_digest.py"])
                    argv = parts[2:]
                else:
                    self.assertEqual(parts[:3], ["uv", "run", "changelog_digest.py"])
                    argv = parts[3:]
                code, stdout, stderr = run_main(argv)
                if "--dry-run" in parts:
                    self.assertEqual(code, 0, stderr)
                    if argv == sample_argv:
                        self.assertEqual(stdout, expected)
                else:
                    self.assertEqual(code, 1, stderr)
                    self.assertIn("uv run changelog_digest.py", stderr)
                    self.assertEqual(stdout, "")
            self.assertFalse(os.path.exists(os.path.join(ROOT, "digests")))
        finally:
            os.chdir(previous)

    def test_12_dry_run_without_sdk(self):
        """source: a dry run prints the prompt even when the model package is missing."""
        install_module(self, None)
        with tempfile.TemporaryDirectory() as directory:
            path = write_text(directory, "log.md", entry_log())
            try:
                code, stdout, stderr = run_main([path, "--date", "2026-03-14", "--dry-run"])
            except ImportError as exc:
                self.fail("the dry run needed the anthropic package: %s" % exc)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("Generate the digest in Markdown format.", stdout)
        self.assertTrue(stdout.endswith("\n"))
        self.assertIn("Moved the peas.", stdout)

    def test_13_nothing_to_do_for_empty_day(self):
        """source: a date with no matching entries finishes with nothing to do, empty output, and no saved file."""
        install_module(self, None)
        with tempfile.TemporaryDirectory() as directory:
            path = write_text(
                directory,
                "log.md",
                "\n".join(
                    [
                        entry_log(day="2026-03-01", title="First"),
                        entry_log(day="2026-03-02", title="Second"),
                    ]
                ),
            )
            out_dir = os.path.join(directory, "out")
            os.mkdir(out_dir)
            code, stdout, stderr = run_main(
                [path, "--date", "2026-03-14", "--out-dir", out_dir]
            )
            self.assertEqual(os.listdir(out_dir), [])
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertTrue(line_startswith(stderr, "nothing to do"))
        self.assertIn(
            "nothing to do: no entries for 2026-03-14 in {0} (2 entries, 2026-03-01 to 2026-03-02)".format(
                path
            ),
            stderr,
        )

    def test_14_no_entries_in_file(self):
        """source: a filtered zero is not "no work", so text with no entry headings returns an error that names the heading format."""
        with tempfile.TemporaryDirectory() as directory:
            path = write_text(directory, "log.md", "# Notes\n\nA paragraph without an entry heading.\n")
            code, stdout, stderr = run_main([path, "--date", "2026-03-14", "--dry-run"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("## 2026-03-14 09:30 - [Project] Title", stderr)
        self.assertTrue(line_startswith(stderr, "error:"))

    def test_15_missing_file(self):
        """source: a missing changelog file is an error and the message says it cannot be read."""
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "missing.md")
            code, stdout, stderr = run_main([path, "--date", "2026-03-14", "--dry-run"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertTrue(line_startswith(stderr, "error: cannot read"))

    def test_16_missing_sdk_does_not_install(self):
        """source: an earlier version of this script installed packages by itself when an import failed."""
        install_module(self, None)
        called = []

        def record(*args, **kwargs):
            called.append((args, kwargs))

        with tempfile.TemporaryDirectory() as directory:
            path = write_text(directory, "log.md", entry_log())
            with mock.patch("os.system", record):
                with mock.patch("subprocess.run", record):
                    with mock.patch("subprocess.Popen", record):
                        code, stdout, stderr = run_main([path, "--date", "2026-03-14"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn('Run this script with "uv run changelog_digest.py"', stderr)
        self.assertEqual(called, [])

    def test_17_model_call_writes_digest_and_optional_file(self):
        """source: a real run calls the model once with the dry-run prompt, prints the digest, and writes a file only when asked."""
        model_text = "The frost date moved the peas."
        module = fake_anthropic()
        install_module(self, module)
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return text_response(model_text)

        module.Messages.on_create = create
        with tempfile.TemporaryDirectory() as directory:
            path = write_text(directory, "log.md", entry_log())
            dry_code, dry_stdout, dry_stderr = run_main(
                [path, "--date", "2026-03-14", "--dry-run"]
            )
            self.assertEqual(dry_code, 0)
            self.assertEqual(dry_stderr, "")
            prompt = dry_stdout[:-1]
            self.assertFalse(prompt.endswith("\n"))
            out_dir = os.path.join(directory, "out")
            code, stdout, stderr = run_main(
                [
                    path,
                    "--date",
                    "2026-03-14",
                    "--model",
                    "example-model",
                    "--out-dir",
                    out_dir,
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                calls[0],
                {
                    "model": "example-model",
                    "max_tokens": cd.MAX_TOKENS,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            self.assertEqual(stdout, model_text + "\n")
            saved = os.path.join(out_dir, "2026-03-14-digest.md")
            with open(saved, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), model_text + "\n")
            self.assertTrue(line_startswith(stderr, "saved:"))
            calls.clear()
            work = os.path.join(directory, "work")
            os.mkdir(work)
            previous = os.getcwd()
            os.chdir(work)
            try:
                code, stdout, stderr = run_main([path, "--date", "2026-03-14"])
                self.assertEqual(os.listdir(work), [])
            finally:
                os.chdir(previous)
            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["model"], cd.DEFAULT_MODEL)
            self.assertEqual(calls[0]["max_tokens"], cd.MAX_TOKENS)
            self.assertEqual(calls[0]["messages"], [{"role": "user", "content": prompt}])
            self.assertEqual(stdout, model_text + "\n")

    def test_18_digest_filename(self):
        """source: daily and weekly digest file names follow the date and the week flag."""
        day = datetime.date(2026, 3, 14)
        self.assertEqual(cd.digest_filename(day, False), "2026-03-14-digest.md")
        self.assertEqual(cd.digest_filename(day, True), "2026-03-14-weekly-digest.md")

    def test_19_request_digest_skips_thinking_block(self):
        """source: message.content[0].text breaks when the first block is a thinking block."""
        blocks = [
            types.SimpleNamespace(type="thinking"),
            types.SimpleNamespace(type="text", text="alpha"),
            types.SimpleNamespace(type="text", text="beta"),
        ]
        seen = {}

        def create(**kwargs):
            seen.update(kwargs)
            return text_response(None, blocks=blocks)

        client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
        try:
            text, truncated = cd.request_digest(client, "the prompt", "example-model")
        except AttributeError as exc:
            self.fail("request_digest read a block that has no text: %s" % exc)
        self.assertEqual(text, "alphabeta")
        self.assertFalse(truncated)
        self.assertEqual(seen["messages"], [{"role": "user", "content": "the prompt"}])
        self.assertEqual(seen["max_tokens"], cd.MAX_TOKENS)
        self.assertEqual(seen["model"], "example-model")

    def test_20_stop_reasons_refusal_and_max_tokens(self):
        """source: a refusal is an error, and a digest cut off at the token limit is still printed with a warning."""
        module = fake_anthropic()
        install_module(self, module)
        with tempfile.TemporaryDirectory() as directory:
            path = write_text(directory, "log.md", entry_log())
            module.Messages.on_create = lambda **kwargs: text_response(
                "nope", stop_reason="refusal"
            )
            code, stdout, stderr = run_main([path, "--date", "2026-03-14"])
            self.assertEqual(code, 1)
            self.assertEqual(stdout, "")
            self.assertIn("error: the model declined to write this digest", stderr)
            module.Messages.on_create = lambda **kwargs: text_response(
                "Partial digest", stop_reason="max_tokens"
            )
            code, stdout, stderr = run_main([path, "--date", "2026-03-14"])
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "Partial digest\n")
        self.assertIn(
            "warning: the digest was cut off at the {0} token limit".format(cd.MAX_TOKENS),
            stderr,
        )

    def test_21_typeerror_authentication_and_other(self):
        """source: a missing credential reports a hint, and any other type error propagates."""
        module = fake_anthropic()
        install_module(self, module)
        with tempfile.TemporaryDirectory() as directory:
            path = write_text(directory, "log.md", entry_log())
            module.Messages.on_create = lambda **kwargs: (_ for _ in ()).throw(
                TypeError("Could not resolve authentication method")
            )
            code, stdout, stderr = run_main([path, "--date", "2026-03-14"])
            self.assertEqual(code, 1)
            self.assertEqual(stdout, "")
            self.assertIn(
                "error: no Anthropic credential found. Set ANTHROPIC_API_KEY, or use --dry-run and paste the prompt into any chat model.",
                stderr,
            )
            module.Messages.on_create = lambda **kwargs: (_ for _ in ()).throw(
                TypeError("boom")
            )
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()
            with self.assertRaises(TypeError) as caught:
                with contextlib.redirect_stdout(stdout_buffer):
                    with contextlib.redirect_stderr(stderr_buffer):
                        cd.main([path, "--date", "2026-03-14"])
            self.assertEqual(str(caught.exception), "boom")

    def test_22_not_found_and_api_status(self):
        """source: an unknown model and a plain API status error are both reported."""
        module = fake_anthropic()
        install_module(self, module)
        with tempfile.TemporaryDirectory() as directory:
            path = write_text(directory, "log.md", entry_log())
            module.Messages.on_create = lambda **kwargs: (_ for _ in ()).throw(
                module.NotFoundError("missing", status_code=404)
            )
            code, stdout, stderr = run_main(
                [path, "--date", "2026-03-14", "--model", "example-model"]
            )
            self.assertEqual(code, 1)
            self.assertEqual(stdout, "")
            self.assertIn("example-model", stderr)
            self.assertIn("was not found or is not available", stderr)
            module.Messages.on_create = lambda **kwargs: (_ for _ in ()).throw(
                module.APIStatusError("overloaded", status_code=500)
            )
            code, stdout, stderr = run_main([path, "--date", "2026-03-14"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("500", stderr)
        self.assertIn("overloaded", stderr)
        self.assertIn("error: the Anthropic API returned status 500: overloaded", stderr)

    def test_23_constructor_anthropic_error(self):
        """source: an SDK error raised while creating the client is reported."""
        module = fake_anthropic()
        install_module(self, module)

        def fail():
            raise module.AnthropicError("no profile")

        module.Anthropic.on_init = fail
        with tempfile.TemporaryDirectory() as directory:
            path = write_text(directory, "log.md", entry_log())
            try:
                code, stdout, stderr = run_main([path, "--date", "2026-03-14"])
            except module.AnthropicError as exc:
                self.fail("an SDK error from creating the client escaped main: %s" % exc)
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("no profile", stderr)
        self.assertIn("error: the Anthropic SDK could not complete the request: no profile", stderr)

    def test_24_off_format_heading_in_range(self):
        """source: a filtered zero is not "no work" when headings in range do not match the entry format."""
        text = "\n".join(
            [
                "## 2026-03-12 - [garden-planner] Soil log",
                "",
                "**Commit:** 9e8d7c6",
                "**Session:** Soil log",
                "",
                "### What Changed",
                "Stored a note on each bed.",
                "",
                "## 2026-03-14 | recipe-box | Timers",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = write_text(directory, "log.md", text)
            code, stdout, stderr = run_main(
                [path, "--date", "2026-03-14", "--dry-run"]
            )
            self.assertEqual(code, 1)
            self.assertEqual(stdout, "")
            self.assertTrue(line_startswith(stderr, "error:"))
            self.assertIn("first at line 9", stderr)
            self.assertIn("## 2026-03-14 09:30 - [Project] Title", stderr)
            code, stdout, stderr = run_main(
                [path, "--week", "--date", "2026-03-14", "--dry-run"]
            )
        self.assertEqual(code, 0)
        self.assertIn("weekly development digest", stdout)
        self.assertIn("Soil log", stdout)
        self.assertNotIn("Timers", stdout)
        self.assertTrue(line_startswith(stderr, "warning:"))
        self.assertIn("first at line 9", stderr)

    def test_25_skipped_heading_after_separator(self):
        """source: an off-format heading after a separator is still reported, and a later entry heading still starts an entry."""
        text = "\n".join(
            [
                "## 2026-03-12 - [garden-planner] First",
                "",
                "### What Changed",
                "Alpha.",
                "",
                "---",
                "## shed notes are not an entry",
                "LEAKED_BODY",
                "## 2026-03-14 11:00 - [recipe-box] Second",
                "",
                "### What Changed",
                "Beta.",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].title, "First")
        self.assertEqual(result.entries[0].section("What Changed"), "Alpha.")
        self.assertEqual(result.entries[1].title, "Second")
        self.assertEqual(result.entries[1].project, "recipe-box")
        self.assertEqual(result.entries[1].section("What Changed"), "Beta.")
        self.assertNotIn("LEAKED_BODY", result.entries[0].section("What Changed"))
        self.assertNotIn("LEAKED_BODY", result.entries[1].section("What Changed"))
        self.assertTrue(
            any(line == "## shed notes are not an entry" for _number, line in result.skipped)
        )

    def test_26_out_dir_is_a_file(self):
        """source: when the output directory path is an existing file, the digest is still printed and the write error is reported."""
        module = fake_anthropic()
        install_module(self, module)
        module.Messages.on_create = lambda **kwargs: text_response("Saved nowhere.")
        with tempfile.TemporaryDirectory() as directory:
            path = write_text(directory, "log.md", entry_log())
            blocker = os.path.join(directory, "not-a-dir")
            write_text(directory, "not-a-dir", "x")
            try:
                code, stdout, stderr = run_main(
                    [path, "--date", "2026-03-14", "--out-dir", blocker]
                )
            except OSError as exc:
                self.fail("the write error escaped main: %s" % exc)
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "Saved nowhere.\n")
        self.assertTrue(line_startswith(stderr, "error: cannot write"))

    def test_27_inline_script_metadata(self):
        """source: rule, the script declares its own dependency, so uv run works without a project file."""
        path = os.path.join(ROOT, "changelog_digest.py")
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(
            lines[:4],
            [
                "# /// script",
                '# requires-python = ">=3.10"',
                '# dependencies = ["anthropic>=1.0"]',
                "# ///",
            ],
        )
        future = "from __future__ import annotations"
        import_indexes = [
            index
            for index, line in enumerate(lines)
            if line.startswith("import ") or line.startswith("from ")
        ]
        self.assertTrue(import_indexes)
        self.assertEqual(lines[import_indexes[0]], future)

    def test_28_byte_order_mark(self):
        """source: review, a changelog saved with a UTF-8 byte order mark lost its first entry without a report."""
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "log.md")
            with open(path, "wb") as handle:
                handle.write(b"\xef\xbb\xbf" + entry_log().encode("utf-8"))
            code, stdout, stderr = run_main(
                [path, "--date", "2026-03-14", "--dry-run"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("Moved the peas.", stdout)

    def test_29_backtick_fence_keeps_tilde_heading_and_separator(self):
        """source: review, a backtick fence closed on a tilde line, so a heading and a separator inside the block were read as changelog structure."""
        text = "\n".join(
            [
                "## 2026-03-14 09:30 - [garden-planner] Real",
                "",
                "### What Changed",
                "`" * 3,
                "~" * 3,
                "## Not a heading",
                "---",
                "`" * 3,
                "After.",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(result.skipped, ())
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].title, "Real")
        self.assertEqual(
            result.entries[0].section("What Changed"),
            "\n".join(
                [
                    "`" * 3,
                    "~" * 3,
                    "## Not a heading",
                    "---",
                    "`" * 3,
                    "After.",
                ]
            ),
        )

    def test_30_tilde_fence_keeps_backtick_heading_and_separator(self):
        """source: review, a tilde fence closed on a backtick line, so a heading and a separator inside the block were read as changelog structure."""
        text = "\n".join(
            [
                "## 2026-03-14 09:30 - [garden-planner] Real",
                "",
                "### What Changed",
                "~" * 3,
                "`" * 3,
                "## Not a heading",
                "---",
                "~" * 3,
                "After.",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(result.skipped, ())
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].title, "Real")
        self.assertEqual(
            result.entries[0].section("What Changed"),
            "\n".join(
                [
                    "~" * 3,
                    "`" * 3,
                    "## Not a heading",
                    "---",
                    "~" * 3,
                    "After.",
                ]
            ),
        )

    def test_31_four_backtick_fence_not_closed_by_three(self):
        """source: review, a four-backtick fence was closed by a three-backtick line, so a heading and a separator inside it were read as changelog structure."""
        text = "\n".join(
            [
                "## 2026-03-14 09:30 - [garden-planner] Real",
                "",
                "### What Changed",
                "`" * 4,
                "`" * 3,
                "## Not a heading",
                "---",
                "`" * 4,
                "After.",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(result.skipped, ())
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].title, "Real")
        self.assertEqual(
            result.entries[0].section("What Changed"),
            "\n".join(
                [
                    "`" * 4,
                    "`" * 3,
                    "## Not a heading",
                    "---",
                    "`" * 4,
                    "After.",
                ]
            ),
        )

    def test_32_truncated_digest_file_ends_with_cutoff_line(self):
        """source: review, a digest cut off at the token limit was saved to --out-dir as if it were complete."""
        module = fake_anthropic()
        install_module(self, module)
        cutoff = "[cut off at the {0} token limit]".format(cd.MAX_TOKENS)
        with tempfile.TemporaryDirectory() as directory:
            path = write_text(directory, "log.md", entry_log())
            out_dir = os.path.join(directory, "out")
            module.Messages.on_create = lambda **kwargs: text_response(
                "Partial digest", stop_reason="max_tokens"
            )
            code, stdout, stderr = run_main(
                [path, "--date", "2026-03-14", "--out-dir", out_dir]
            )
            self.assertEqual(code, 0)
            self.assertEqual(stdout, "Partial digest\n")
            self.assertNotIn(cutoff, stdout)
            self.assertIn(
                "warning: the digest was cut off at the {0} token limit".format(
                    cd.MAX_TOKENS
                ),
                stderr,
            )
            saved = os.path.join(out_dir, "2026-03-14-digest.md")
            with open(saved, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "Partial digest\n" + cutoff + "\n")
            module.Messages.on_create = lambda **kwargs: text_response("Full digest")
            code, stdout, stderr = run_main(
                [path, "--date", "2026-03-14", "--out-dir", out_dir]
            )
            self.assertEqual(code, 0)
            self.assertEqual(stdout, "Full digest\n")
            with open(saved, encoding="utf-8") as handle:
                written = handle.read()
            self.assertEqual(written, "Full digest\n")
            self.assertNotIn(cutoff, written)

    def test_33_tilde_fence_alone_keeps_heading_and_separator(self):
        """source: planted fault D23: tilde fences could stop being recognized without any test failing."""
        text = "\n".join(
            [
                "## 2026-03-14 09:30 - [garden-planner] Real",
                "",
                "### What Changed",
                "~" * 3,
                "## Not a heading",
                "---",
                "~" * 3,
                "After.",
                "---",
                "## 2026-03-15 09:30 - [garden-planner] Next",
                "",
                "### What Changed",
                "Kept the beans.",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(result.skipped, ())
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(
            result.entries[0].section("What Changed"),
            "\n".join(
                [
                    "~" * 3,
                    "## Not a heading",
                    "---",
                    "~" * 3,
                    "After.",
                ]
            ),
        )
        self.assertEqual(result.entries[1].title, "Next")

    def test_34_default_run_sends_sonnet_5_5_and_low_effort(self):
        """source: a default run calls messages.create with claude-sonnet-5-5 and output_config effort low."""
        self.assertEqual(cd.DEFAULT_MODEL, "claude-sonnet-5-5")
        self.assertEqual(cd.DEFAULT_EFFORT, "low")
        self.assertEqual(cd.EFFORT_LEVELS, ("low", "medium", "high", "xhigh", "max"))
        code, stdout, stderr, calls = run_with_model(self, [])
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "A digest.\n")
        self.assertEqual(stderr, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "claude-sonnet-5-5")
        self.assertIn("output_config", calls[0])
        self.assertEqual(calls[0]["output_config"], {"effort": "low"})
        self.assertEqual(calls[0]["max_tokens"], cd.MAX_TOKENS)

    def test_35_other_model_omits_output_config(self):
        """source: --model some-other-model calls messages.create with no output_config key."""
        code, stdout, stderr, calls = run_with_model(
            self, ["--model", "some-other-model"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "A digest.\n")
        self.assertEqual(stderr, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "some-other-model")
        self.assertNotIn("output_config", calls[0])
        self.assertEqual(set(calls[0]), {"model", "max_tokens", "messages"})

    def test_36_other_model_sends_given_effort(self):
        """source: --model some-other-model --effort medium sends output_config effort medium."""
        code, stdout, stderr, calls = run_with_model(
            self, ["--model", "some-other-model", "--effort", "medium"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "A digest.\n")
        self.assertEqual(stderr, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "some-other-model")
        self.assertIn("output_config", calls[0])
        self.assertEqual(calls[0]["output_config"], {"effort": "medium"})

    def test_37_default_model_sends_given_effort(self):
        """source: --effort high with the default model sends output_config effort high."""
        code, stdout, stderr, calls = run_with_model(self, ["--effort", "high"])
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "A digest.\n")
        self.assertEqual(stderr, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "claude-sonnet-5-5")
        self.assertIn("output_config", calls[0])
        self.assertEqual(calls[0]["output_config"], {"effort": "high"})

    def test_38_bogus_effort_exits_2_and_calls_nothing(self):
        """source: --effort bogus exits 2 and calls nothing."""
        module = fake_anthropic()
        install_module(self, module)
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return text_response("A digest.")

        module.Messages.on_create = create
        with tempfile.TemporaryDirectory() as directory:
            path = write_text(directory, "log.md", entry_log())
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()
            with self.assertRaises(SystemExit) as caught:
                with contextlib.redirect_stdout(stdout_buffer):
                    with contextlib.redirect_stderr(stderr_buffer):
                        cd.main([path, "--date", "2026-03-14", "--effort", "bogus"])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(calls, [])
        self.assertEqual(stdout_buffer.getvalue(), "")
        self.assertIn("invalid choice", stderr_buffer.getvalue())

    def test_39_request_digest_effort_none_and_max(self):
        """source: request_digest with effort None passes no output_config key, and effort max passes it."""
        seen = []

        def create(**kwargs):
            seen.append(kwargs)
            return text_response("digest text")

        client = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))
        text, truncated = cd.request_digest(
            client, "the prompt", "example-model", effort=None
        )
        self.assertEqual(text, "digest text")
        self.assertFalse(truncated)
        self.assertNotIn("output_config", seen[0])
        self.assertEqual(set(seen[0]), {"model", "max_tokens", "messages"})
        self.assertEqual(seen[0]["model"], "example-model")
        self.assertEqual(seen[0]["max_tokens"], cd.MAX_TOKENS)
        self.assertEqual(
            seen[0]["messages"], [{"role": "user", "content": "the prompt"}]
        )
        text, truncated = cd.request_digest(
            client, "the prompt", "example-model", effort="max"
        )
        self.assertEqual(text, "digest text")
        self.assertFalse(truncated)
        self.assertIn("output_config", seen[1])
        self.assertEqual(seen[1]["output_config"], {"effort": "max"})
        self.assertEqual(seen[1]["model"], "example-model")
        self.assertEqual(seen[1]["max_tokens"], cd.MAX_TOKENS)
        self.assertEqual(
            seen[1]["messages"], [{"role": "user", "content": "the prompt"}]
        )

    def test_40_readme_names_current_default_model(self):
        """source: the README names claude-sonnet-5-5 as the default and no longer names claude-sonnet-4-6."""
        readme_path = os.path.join(ROOT, "README.md")
        with open(readme_path, encoding="utf-8") as handle:
            readme = handle.read()
        self.assertIn(
            "| `--model MODEL` | Model id. Defaults to `claude-sonnet-5-5`. |\n"
            "| `--effort LEVEL` | How much the model thinks: `low`, `medium`, `high`, `xhigh` or `max`. Defaults to `low` with the default model. With another model it is sent only when you pass it. |",
            readme,
        )
        self.assertIn(
            "A digest cut off at the token limit is saved with a last line saying so. The 16,000 token limit covers the model's thinking as well as the digest text.",
            readme,
        )
        self.assertNotIn("claude-sonnet-4-6", readme)

    def test_41_inline_backtick_sample_does_not_swallow_later_entry(self):
        """source: a one-line inline sample that starts with three backticks and has more backticks on that line was read as a fence that never closed, so later entries landed under the wrong project and day."""
        title_b = "Beta entry title"
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                "`" * 3 + " npm install " + "`" * 3,
                "## 2026-03-15 - [beta] {0}".format(title_b),
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[0].date, datetime.date(2026, 3, 14))
        self.assertEqual(result.entries[1].project, "beta")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 15))
        self.assertEqual(result.entries[1].title, title_b)
        day_a = datetime.date(2026, 3, 14)
        day_b = datetime.date(2026, 3, 15)
        prompt_a = cd.build_daily_prompt(cd.entries_for_date(result.entries, day_a), day_a)
        prompt_b = cd.build_daily_prompt(cd.entries_for_date(result.entries, day_b), day_b)
        self.assertIn("npm install", prompt_a)
        self.assertNotIn(title_b, prompt_a)
        self.assertIn(title_b, prompt_b)

    def test_42_four_space_backticks_do_not_swallow_later_entry(self):
        """source: a four-space backtick opener that never closes is ordinary text, so the following heading stays its own entry."""
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                " " * 4 + "`" * 3,
                " " * 4 + "sample text",
                "## 2026-03-15 - [beta] Beta entry title",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[0].date, datetime.date(2026, 3, 14))
        self.assertEqual(result.entries[1].project, "beta")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 15))

    def test_43_four_space_tildes_do_not_swallow_later_entry(self):
        """source: a four-space tilde opener that never closes is ordinary text, so the following heading stays its own entry."""
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                " " * 4 + "~" * 3,
                "## 2026-03-15 - [beta] Beta entry title",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[0].date, datetime.date(2026, 3, 14))
        self.assertEqual(result.entries[1].project, "beta")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 15))

    def test_44_three_space_fence_keeps_inner_heading(self):
        """source: a fence indented three spaces was at risk of being ignored, which would drop the inner heading from the entry body or turn it into another entry."""
        fence = " " * 3 + "`" * 3
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                fence,
                "## not a heading",
                fence,
                "## 2026-03-15 - [beta] Beta entry title",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[0].date, datetime.date(2026, 3, 14))
        self.assertEqual(result.entries[1].project, "beta")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 15))
        body = "\n".join(section for _label, section in result.entries[0].sections)
        self.assertIn("## not a heading", body)
        self.assertEqual(result.skipped, ())
        self.assertEqual(
            [entry.project for entry in result.entries],
            ["alpha", "beta"],
        )

    def test_45_tilde_opener_info_may_hold_backtick(self):
        """source: a tilde fence opener whose info text holds a backtick must still open, so a heading inside it is not parsed as its own entry."""
        opener = "~" * 3 + " info " + "`"
        closer = "~" * 3
        inside = "## 2026-03-15 - [beta] Inside title"
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                opener,
                inside,
                closer,
                "## 2026-03-16 - [gamma] Outside title",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[1].project, "gamma")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 16))
        body = "\n".join(section for _label, section in result.entries[0].sections)
        self.assertIn(inside, body)
        self.assertNotIn("Inside title", result.entries[1].title)

    def test_46_old_package_output_config_typeerror(self):
        """source: Messages.create raising TypeError for an unexpected output_config argument escaped as a traceback when the installed anthropic package was too old for the effort setting."""
        module = fake_anthropic()
        install_module(self, module)
        module.Messages.on_create = lambda **kwargs: (_ for _ in ()).throw(
            TypeError(
                "Messages.create() got an unexpected keyword argument 'output_config'"
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            path = write_text(directory, "log.md", entry_log())
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()
            escaped = False
            try:
                with contextlib.redirect_stdout(stdout_buffer):
                    with contextlib.redirect_stderr(stderr_buffer):
                        code = cd.main([path, "--date", "2026-03-14"])
            except Exception:
                escaped = True
            stdout = stdout_buffer.getvalue()
            stderr = stderr_buffer.getvalue()
        self.assertFalse(escaped)
        self.assertEqual(code, 1)
        self.assertIn(
            "error: the installed anthropic package is too old for the effort setting. Run the script with uv run, which installs a current one.",
            stderr,
        )
        self.assertNotIn("Traceback", stdout)
        self.assertNotIn("Traceback", stderr)

    def test_47_tab_indented_backticks_do_not_swallow_later_entry(self):
        """source: a tab-indented backtick opener that never closes is ordinary text, so the following heading stays its own entry."""
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                "\t" * 1 + "`" * 3,
                " " * 4 + "sample text",
                "## 2026-03-15 - [beta] Beta entry title",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[0].date, datetime.date(2026, 3, 14))
        self.assertEqual(result.entries[1].project, "beta")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 15))

    def test_48_four_space_list_fence_keeps_inner_heading_and_separator(self):
        """source: a fence nested in a list item was not recognized once four-column indents were excluded."""
        fence = " " * 4 + "`" * 3
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                fence,
                "## not a heading",
                "---",
                fence,
                "## 2026-03-15 - [beta] Beta entry title",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[0].date, datetime.date(2026, 3, 14))
        self.assertEqual(result.entries[1].project, "beta")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 15))
        self.assertEqual(result.entries[1].title, "Beta entry title")
        body = "\n".join(section for _label, section in result.entries[0].sections)
        self.assertIn("## not a heading", body)
        self.assertIn("---", body)
        self.assertEqual(result.skipped, ())

    def test_49_six_space_list_fence_keeps_inner_heading_and_separator(self):
        """source: a fence nested in a list item indented six spaces was not recognized once four-column indents were excluded."""
        fence = " " * 6 + "`" * 3
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                fence,
                "## not a heading",
                "---",
                fence,
                "## 2026-03-15 - [beta] Beta entry title",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[0].date, datetime.date(2026, 3, 14))
        self.assertEqual(result.entries[1].project, "beta")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 15))
        self.assertEqual(result.entries[1].title, "Beta entry title")
        body = "\n".join(section for _label, section in result.entries[0].sections)
        self.assertIn("## not a heading", body)
        self.assertIn("---", body)
        self.assertEqual(result.skipped, ())

    def test_50_tab_list_fence_keeps_inner_heading_and_separator(self):
        """source: a fence nested in a list item indented with a tab was not recognized once four-column indents were excluded."""
        fence = "\t" + "`" * 3
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                fence,
                "## not a heading",
                "---",
                fence,
                "## 2026-03-15 - [beta] Beta entry title",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[0].date, datetime.date(2026, 3, 14))
        self.assertEqual(result.entries[1].project, "beta")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 15))
        self.assertEqual(result.entries[1].title, "Beta entry title")
        body = "\n".join(section for _label, section in result.entries[0].sections)
        self.assertIn("## not a heading", body)
        self.assertIn("---", body)
        self.assertEqual(result.skipped, ())

    def test_51_unclosed_fence_does_not_swallow_later_entry(self):
        """source: a fence that never closes swallowed every later entry."""
        opener = "`" * 3
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                opener,
                "## 2026-03-15 - [beta] Beta entry title",
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[0].date, datetime.date(2026, 3, 14))
        self.assertEqual(result.entries[1].project, "beta")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 15))
        body = "\n".join(section for _label, section in result.entries[0].sections)
        self.assertIn(opener, body)

    def test_52_inline_sample_then_closed_fence_stays_two_entries(self):
        """source: a one-line inline backtick sample does not open a fence, including when a closed fence follows in the next entry."""
        inline = "`" * 3 + " npm install " + "`" * 3
        fence = "`" * 3
        inside = "## not a heading"
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                inline,
                "## 2026-03-15 - [beta] Beta entry title",
                fence,
                inside,
                "---",
                fence,
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[0].date, datetime.date(2026, 3, 14))
        self.assertEqual(result.entries[1].project, "beta")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 15))
        self.assertEqual(result.entries[1].title, "Beta entry title")
        first_body = "\n".join(
            section for _label, section in result.entries[0].sections
        )
        second_body = "\n".join(
            section for _label, section in result.entries[1].sections
        )
        self.assertIn(inline, first_body)
        self.assertIn(inside, second_body)
        self.assertIn("---", second_body)
        self.assertEqual(result.skipped, ())

    def test_53_unclosed_fence_stops_at_entry_boundary(self):
        """source: an unclosed fence in one entry paired with a real fence in a later entry and swallowed that entry's heading."""
        opener = "`" * 3 + "bash"
        fence = "`" * 3
        inside = "## not a heading"
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                opener,
                "---",
                "## 2026-03-15 - [beta] Beta entry title",
                fence,
                inside,
                fence,
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[0].date, datetime.date(2026, 3, 14))
        self.assertEqual(result.entries[1].project, "beta")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 15))
        first_body = "\n".join(
            section for _label, section in result.entries[0].sections
        )
        second_body = "\n".join(
            section for _label, section in result.entries[1].sections
        )
        self.assertIn(opener, first_body)
        self.assertIn(inside, second_body)
        self.assertEqual(result.skipped, ())

    def test_54_four_space_unclosed_fence_stops_at_entry_boundary(self):
        """source: an unclosed fence indented four spaces in one entry paired with a real fence in a later entry and swallowed that entry's heading."""
        opener = " " * 4 + "`" * 3 + "bash"
        fence = "`" * 3
        inside = "## not a heading"
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                opener,
                "---",
                "## 2026-03-15 - [beta] Beta entry title",
                fence,
                inside,
                fence,
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[0].date, datetime.date(2026, 3, 14))
        self.assertEqual(result.entries[1].project, "beta")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 15))
        first_body = "\n".join(
            section for _label, section in result.entries[0].sections
        )
        second_body = "\n".join(
            section for _label, section in result.entries[1].sections
        )
        self.assertIn(opener, first_body)
        self.assertIn(inside, second_body)
        self.assertEqual(result.skipped, ())

    def test_55_two_stray_openers_leave_three_entries(self):
        """source: stray openers in two entries paired with a real fence in a third entry and swallowed the later headings."""
        opener = "`" * 3 + "bash"
        fence = "`" * 3
        inside = "## not a heading"
        text = "\n".join(
            [
                "## 2026-03-14 - [alpha] Alpha title",
                opener,
                "---",
                "## 2026-03-15 - [beta] Beta title",
                opener,
                "---",
                "## 2026-03-16 - [gamma] Gamma title",
                fence,
                inside,
                fence,
            ]
        )
        result = cd.parse_changelog(text)
        self.assertEqual(len(result.entries), 3)
        self.assertEqual(result.entries[0].project, "alpha")
        self.assertEqual(result.entries[0].date, datetime.date(2026, 3, 14))
        self.assertEqual(result.entries[1].project, "beta")
        self.assertEqual(result.entries[1].date, datetime.date(2026, 3, 15))
        self.assertEqual(result.entries[2].project, "gamma")
        self.assertEqual(result.entries[2].date, datetime.date(2026, 3, 16))
        body = "\n".join(section for _label, section in result.entries[2].sections)
        self.assertIn(inside, body)
        self.assertEqual(result.skipped, ())

    def test_56_four_hundred_unclosed_openers_stay_separate_entries(self):
        """source: each unclosed fence kept scanning past the entry boundary through the rest of the file."""
        opener = "`" * 3 + "bash"
        lines = []
        for index in range(400):
            lines.append("## 2026-03-14 - [p{0}] Title {0}".format(index))
            lines.append(opener)
            lines.append("---")
        result = cd.parse_changelog("\n".join(lines))
        self.assertEqual(len(result.entries), 400)
        self.assertEqual(
            [entry.project for entry in result.entries],
            ["p{0}".format(index) for index in range(400)],
        )
        self.assertEqual(result.skipped, ())


if __name__ == "__main__":
    unittest.main()
