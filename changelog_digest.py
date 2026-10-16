# /// script
# requires-python = ">=3.10"
# dependencies = ["anthropic>=1.0"]
# ///
"""Read a local markdown changelog and ask a Claude model for a digest.

`uv run changelog_digest.py CHANGELOG --date YYYY-MM-DD` runs it with its
dependency installed, while `--dry-run` needs nothing installed.
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "low"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
MAX_TOKENS = 16000
PROMPT_SKIP_SECTIONS = ("files modified",)

_ENTRY_SHAPE = "## 2026-03-14 09:30 - [Project] Title"
_ENTRY_RE = re.compile(
    r"^## (\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))? - \[([^\]]+)\] (.+?)\s*$"
)
_FIELD_RE = re.compile(r"^\*\*([^*:]+):\*\*\s*(.*)$")

DAILY_TEMPLATE = """\
You are a content creator helping generate daily development digests for a YouTube channel about building software products.

Given the following development sessions from {date}, create an engaging daily digest that:

1. **Opens with a hook** - What's the most interesting thing that happened today?
2. **Tells the story** - What was I trying to do? What happened? What did I learn?
3. **Highlights breakthroughs** - Any "aha" moments or discoveries worth sharing
4. **Notes challenges** - What was hard? What would I do differently?
5. **Teases what's next** - Build anticipation for tomorrow
6. **Suggests video ideas** - 2-3 potential YouTube video topics based on today's work

The tone should be authentic, conversational, and educational. Think "building in public" vibes.

Today's development sessions:

{entries_text}

Generate the digest in Markdown format."""

WEEKLY_TEMPLATE = """\
You are a content creator generating a weekly development digest for a YouTube channel.

Given the following week's development work (ending {date}), create an engaging weekly recap that:

1. **Big Picture Progress** - What major milestones were reached this week?
2. **Project Updates** - Brief status on each active project
3. **Week's Best Breakthroughs** - Top 3 discoveries or "aha" moments
4. **Lessons Learned** - What did we learn that could help others?
5. **Next Week Preview** - What's the focus for next week?
6. **Video Content Ideas** - 3-5 YouTube video ideas based on this week's work

This week's development sessions:

{entries_text}

Generate the digest in Markdown format."""


class DigestError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    date: datetime.date
    time: str
    project: str
    title: str
    fields: tuple[tuple[str, str], ...]
    sections: tuple[tuple[str, str], ...]
    line: int

    def field(self, label: str) -> str:
        needle = label.lower()
        for name, value in self.fields:
            if name.lower() == needle:
                return value
        return ""

    def section(self, label: str) -> str:
        needle = label.lower()
        for name, body in self.sections:
            if name.lower() == needle:
                return body
        return ""


@dataclass(frozen=True)
class ParseResult:
    entries: tuple[Entry, ...]
    skipped: tuple[tuple[int, str], ...]


class _Draft:
    def __init__(self, date, time, project, title, line):
        self.date = date
        self.time = time
        self.project = project
        self.title = title
        self.line = line
        self.fields = []
        self.sections = []
        self.summary_lines = []
        self.section_label = None
        self.section_lines = []
        self.in_section = False

    def add_line(self, line, plain):
        if not plain and not self.in_section:
            match = _FIELD_RE.match(line)
            if match is not None:
                self.fields.append((match.group(1).strip(), match.group(2).strip()))
                return
        kept = line.rstrip()
        if self.in_section:
            self.section_lines.append(kept)
        else:
            self.summary_lines.append(kept)

    def start_section(self, line):
        if self.in_section:
            self.sections.append((self.section_label, _trim_body(self.section_lines)))
        else:
            body = _trim_body(self.summary_lines)
            if body:
                self.sections.append(("Summary", body))
            self.in_section = True
        self.summary_lines = []
        self.section_label = line[len("### ") :].strip()
        self.section_lines = []

    def finish(self):
        if self.in_section:
            self.sections.append((self.section_label, _trim_body(self.section_lines)))
        else:
            body = _trim_body(self.summary_lines)
            if body:
                self.sections.append(("Summary", body))
        return Entry(
            date=self.date,
            time=self.time,
            project=self.project,
            title=self.title,
            fields=tuple(self.fields),
            sections=tuple(self.sections),
            line=self.line,
        )


def _trim_body(lines):
    start = 0
    end = len(lines)
    while start < end and lines[start] == "":
        start += 1
    while end > start and lines[end - 1] == "":
        end -= 1
    return "\n".join(lines[start:end])


def _valid_time(value):
    hour_text, minute_text = value.split(":")
    hour = int(hour_text)
    minute = int(minute_text)
    return 0 <= hour <= 23 and 0 <= minute <= 59


def _parse_heading(line, line_number):
    match = _ENTRY_RE.match(line)
    if match is None:
        return None
    date_text, time_text, project, title = match.groups()
    try:
        day = datetime.date.fromisoformat(date_text)
    except ValueError:
        return None
    if time_text is not None and not _valid_time(time_text):
        return None
    return _Draft(
        date=day,
        time=time_text or "",
        project=project.strip(),
        title=title.strip(),
        line=line_number,
    )


def _fence_run(stripped):
    if stripped == "":
        return None
    marker = stripped[0]
    if marker != "`" and marker != "~":
        return None
    length = 0
    for char in stripped:
        if char != marker:
            break
        length += 1
    if length < 3:
        return None
    return (marker, length, stripped[length:])


def _line_fence(line):
    return _fence_run(line.strip())


def _opens_fence(run):
    marker, _length, rest = run
    if marker == "`" and "`" in rest:
        return False
    return True


def _closes_fence(run, marker, length):
    return (
        run is not None
        and run[0] == marker
        and run[1] >= length
        and run[2].strip(" ") == ""
    )


def _is_entry_heading(line):
    if not line.startswith("## "):
        return False
    return _parse_heading(line, 1) is not None


def _entry_boundary_indexes(lines):
    """Indexes of `---` lines whose next non-blank line is an entry heading."""
    boundaries = set()
    last = len(lines)
    index = 0
    while index < last:
        if lines[index].strip() == "---":
            scan = index + 1
            while scan < last and lines[scan].strip() == "":
                scan += 1
            if scan < last and _is_entry_heading(lines[scan]):
                boundaries.add(index)
        index += 1
    return boundaries


def _paired_fence_closers(lines):
    closers = {}
    boundaries = _entry_boundary_indexes(lines)
    index = 0
    last = len(lines)
    while index < last:
        run = _line_fence(lines[index])
        if run is not None and _opens_fence(run):
            marker, length, _rest = run
            closer_index = None
            scan = index + 1
            while scan < last:
                if scan in boundaries:
                    break
                if _closes_fence(_line_fence(lines[scan]), marker, length):
                    closer_index = scan
                    break
                scan += 1
            if closer_index is not None:
                closers[index] = closer_index
                index = closer_index + 1
                continue
        index += 1
    return closers


def parse_changelog(text: str) -> ParseResult:
    entries = []
    skipped = []
    lines = text.splitlines()
    closer_at = _paired_fence_closers(lines)
    fence_until = None
    current = None

    def close_entry():
        nonlocal current
        if current is None:
            return
        entries.append(current.finish())
        current = None

    for index, line in enumerate(lines):
        line_number = index + 1
        stripped = line.strip()
        if fence_until is not None:
            if current is not None:
                current.add_line(line, True)
            if index == fence_until:
                fence_until = None
            continue
        if index in closer_at:
            if current is not None:
                current.add_line(line, True)
            fence_until = closer_at[index]
            continue
        if line.startswith("## "):
            parsed = _parse_heading(line, line_number)
            if parsed is None:
                skipped.append((line_number, line))
                close_entry()
            else:
                close_entry()
                current = parsed
            continue
        if line.startswith("# "):
            close_entry()
            continue
        if stripped == "---":
            close_entry()
            continue
        if current is None:
            continue
        if line.startswith("### "):
            current.start_section(line)
            continue
        current.add_line(line, False)

    close_entry()
    return ParseResult(entries=tuple(entries), skipped=tuple(skipped))


def entries_for_date(entries: Iterable[Entry], day: datetime.date) -> list[Entry]:
    chosen = [entry for entry in entries if entry.date == day]
    chosen.sort(key=lambda entry: (entry.time, entry.line))
    return chosen


def entries_for_week(entries: Iterable[Entry], end: datetime.date) -> list[Entry]:
    start = end - datetime.timedelta(days=6)
    chosen = [entry for entry in entries if start <= entry.date <= end]
    chosen.sort(key=lambda entry: (entry.date, entry.time, entry.line))
    return chosen


def format_entry(entry: Entry) -> str:
    lines = [
        "---",
        "**Project:** {0}".format(entry.project),
        "**Title:** {0}".format(entry.title),
    ]
    if entry.time:
        lines.append("**Time:** {0}".format(entry.time))
    session = entry.field("Session") or "Unnamed session"
    commit = entry.field("Commit") or "N/A"
    lines.append("**Session:** {0}".format(session))
    lines.append("**Commit:** {0}".format(commit))
    for label, value in entry.fields:
        lowered = label.lower()
        if lowered == "session" or lowered == "commit":
            continue
        if value == "":
            continue
        lines.append("**{0}:** {1}".format(label, value))
    for label, body in entry.sections:
        if label.lower() in PROMPT_SKIP_SECTIONS:
            continue
        if body == "":
            continue
        lines.append("")
        lines.append("**{0}:**".format(label))
        lines.append(body)
    return "\n".join(lines)


def _fill(template, date, entries_text):
    before, after = template.split("{date}", 1)
    middle, end = after.split("{entries_text}", 1)
    return before + date + middle + entries_text + end


def build_daily_prompt(entries: Iterable[Entry], day: datetime.date) -> str:
    entries_text = "\n\n".join(format_entry(entry) for entry in entries)
    return _fill(DAILY_TEMPLATE, day.isoformat(), entries_text)


def _one_line(text):
    return re.sub(r"\s+", " ", text)


def build_weekly_prompt(entries: Iterable[Entry], end: datetime.date) -> str:
    groups = {}
    for entry in entries:
        bucket = groups.get(entry.project)
        if bucket is None:
            bucket = []
            groups[entry.project] = bucket
        session = entry.field("Session") or "Unnamed session"
        bucket.append(
            "- [{0}] {1}: {2}".format(entry.date.isoformat(), session, entry.title)
        )
        changed = entry.section("What Changed")
        if changed:
            bucket.append("  - What changed: {0}".format(_one_line(changed)))
        breakthrough = entry.section("Breakthroughs")
        if breakthrough:
            bucket.append("  - Breakthrough: {0}".format(_one_line(breakthrough)))
    blocks = []
    for project, lines in groups.items():
        blocks.append("\n".join(["## {0}".format(project)] + lines))
    entries_text = "\n\n".join(blocks)
    return _fill(WEEKLY_TEMPLATE, end.isoformat(), entries_text)


def request_digest(client: Any, prompt: str, model: str, max_tokens: int = MAX_TOKENS, effort: str | None = None) -> tuple[str, bool]:
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if effort is not None:
        kwargs["output_config"] = {"effort": effort}
    response = client.messages.create(**kwargs)
    if response.stop_reason == "refusal":
        raise DigestError("the model declined to write this digest")
    text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()
    if text == "":
        raise DigestError("the model returned no text")
    return (text, response.stop_reason == "max_tokens")


def digest_filename(day: datetime.date, weekly: bool) -> str:
    if weekly:
        return "{0}-weekly-digest.md".format(day.isoformat())
    return "{0}-digest.md".format(day.isoformat())


def _reason(exc):
    if isinstance(exc, UnicodeDecodeError):
        return exc.reason
    strerror = getattr(exc, "strerror", None)
    if strerror:
        return strerror
    return str(exc)


def _eprint(message):
    print(message, file=sys.stderr)


def _cli_date(value):
    if (
        len(value) != 10
        or value[4] != "-"
        or value[7] != "-"
        or not value.replace("-", "").isdigit()
    ):
        raise argparse.ArgumentTypeError("expected a real YYYY-MM-DD date")
    try:
        return datetime.date(int(value[0:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        raise argparse.ArgumentTypeError("expected a real YYYY-MM-DD date")


def _range_text(day, weekly):
    if not weekly:
        iso = day.isoformat()
        return iso, (iso,)
    start = day - datetime.timedelta(days=6)
    dates = []
    cursor = start
    while cursor <= day:
        dates.append(cursor.isoformat())
        cursor += datetime.timedelta(days=1)
    return "{0} to {1}".format(start.isoformat(), day.isoformat()), tuple(dates)


def _off_format(skipped, dates):
    matched = []
    for line_number, line_text in skipped:
        for date in dates:
            if date in line_text:
                matched.append((line_number, line_text))
                break
    return matched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="changelog_digest.py")
    parser.add_argument("--date", metavar="YYYY-MM-DD", type=_cli_date, default=None)
    parser.add_argument("--week", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out-dir", metavar="DIR")
    parser.add_argument("--model", metavar="MODEL", default=DEFAULT_MODEL)
    parser.add_argument("--effort", metavar="LEVEL", choices=EFFORT_LEVELS, default=None)
    parser.add_argument("changelog")
    args = parser.parse_args(argv)

    day = args.date if args.date is not None else datetime.date.today()
    path = args.changelog
    try:
        with open(path, encoding="utf-8-sig") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        _eprint("error: cannot read {0}: {1}".format(path, _reason(exc)))
        return 1

    parsed = parse_changelog(text)
    if parsed.skipped:
        _eprint(
            'note: skipped {0} "## " heading(s) that do not match the entry format (first at line {1})'.format(
                len(parsed.skipped), parsed.skipped[0][0]
            )
        )
    if not parsed.entries:
        _eprint(
            "error: no changelog entries found in {0}. Entry headings look like: {1}".format(
                path, _ENTRY_SHAPE
            )
        )
        return 1

    if args.week:
        selected = entries_for_week(parsed.entries, day)
    else:
        selected = entries_for_date(parsed.entries, day)
    range_text, range_dates = _range_text(day, args.week)
    off_format = _off_format(parsed.skipped, range_dates)
    if not selected:
        if off_format:
            _eprint(
                "error: {0} heading(s) for {1} do not match the entry format, so nothing could be read (first at line {2}). Entry headings look like: {3}".format(
                    len(off_format), range_text, off_format[0][0], _ENTRY_SHAPE
                )
            )
            return 1
        dates = sorted(entry.date for entry in parsed.entries)
        _eprint(
            "nothing to do: no entries for {0} in {1} ({2} entries, {3} to {4})".format(
                range_text,
                path,
                len(parsed.entries),
                dates[0].isoformat(),
                dates[-1].isoformat(),
            )
        )
        return 0
    if off_format:
        _eprint(
            "warning: {0} heading(s) for {1} do not match the entry format and were left out (first at line {2})".format(
                len(off_format), range_text, off_format[0][0]
            )
        )

    if args.week:
        prompt = build_weekly_prompt(selected, day)
    else:
        prompt = build_daily_prompt(selected, day)
    if args.dry_run:
        sys.stdout.write(prompt + "\n")
        return 0

    if args.effort is not None:
        effort = args.effort
    elif args.model == DEFAULT_MODEL:
        effort = DEFAULT_EFFORT
    else:
        effort = None

    try:
        import anthropic
    except ImportError:
        _eprint(
            'error: the anthropic package is not installed. Run this script with "uv run changelog_digest.py", which installs it.'
        )
        return 1

    try:
        client = anthropic.Anthropic()
        text, truncated = request_digest(client, prompt, args.model, effort=effort)
    except TypeError as exc:
        detail = str(exc)
        if "output_config" in detail:
            _eprint(
                "error: the installed anthropic package is too old for the effort setting. Run the script with uv run, which installs a current one."
            )
            return 1
        if "authentication" in detail:
            _eprint(
                "error: no Anthropic credential found. Set ANTHROPIC_API_KEY, or use --dry-run and paste the prompt into any chat model."
            )
            return 1
        raise
    except anthropic.AuthenticationError:
        _eprint("error: the Anthropic API rejected the credential.")
        return 1
    except anthropic.NotFoundError:
        _eprint(
            "error: model {0} was not found or is not available to this account. Choose another with --model.".format(
                args.model
            )
        )
        return 1
    except anthropic.RateLimitError:
        _eprint("error: rate limited by the Anthropic API. Try again later.")
        return 1
    except anthropic.APIStatusError as exc:
        _eprint(
            "error: the Anthropic API returned status {0}: {1}".format(
                exc.status_code, exc.message
            )
        )
        return 1
    except anthropic.APIConnectionError:
        _eprint("error: could not reach the Anthropic API. Check the network connection.")
        return 1
    except anthropic.AnthropicError as exc:
        _eprint(
            "error: the Anthropic SDK could not complete the request: {0}".format(exc)
        )
        return 1
    except DigestError as exc:
        _eprint("error: {0}".format(exc))
        return 1

    sys.stdout.write(text + "\n")
    if truncated:
        _eprint(
            "warning: the digest was cut off at the {0} token limit".format(MAX_TOKENS)
        )
    if args.out_dir:
        dest = os.path.join(args.out_dir, digest_filename(day, args.week))
        try:
            os.makedirs(args.out_dir, exist_ok=True)
            with open(dest, "w", encoding="utf-8", newline="\n") as handle:
                written = text + "\n"
                if truncated:
                    written += "[cut off at the {0} token limit]\n".format(MAX_TOKENS)
                handle.write(written)
        except OSError as exc:
            _eprint("error: cannot write {0}: {1}".format(dest, _reason(exc)))
            return 1
        _eprint("saved: {0}".format(dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
