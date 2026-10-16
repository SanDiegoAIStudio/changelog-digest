# changelog-digest

changelog-digest reads a local markdown changelog, keeps the entries for one day or for the seven days ending on a date, and asks a Claude model for a digest. The digest has a hook, the story, breakthroughs, challenges, what is next, and video ideas. Pass `--dry-run` to print the prompt and skip the model call.

## Entry format

```text
## 2026-03-14 09:30 - [garden-planner] Frost dates drive the seed calendar

**Commit:** 4f2a9c1
**Session:** Seed calendar

### What Changed
Free text and lists.

### Key Features
- **Frost lookup:** one line per feature.

### Inspiration
Free text.

### Files Modified
- `src/calendar.py` - note

---
```

The time is optional. Put a line that is exactly `---` between entries. A `## ` heading in any other shape is reported and left out.

## Quick start

uv reads the dependency from the script and installs it on the first run.

```bash
uv run changelog_digest.py examples/sample-changelog.md --date 2026-03-14 --dry-run
```

The dry run needs no package, so plain Python 3.9 or newer runs it too:

```bash
python3 changelog_digest.py examples/sample-changelog.md --date 2026-03-14 --dry-run
```

A real run needs `ANTHROPIC_API_KEY`:

```bash
uv run changelog_digest.py examples/sample-changelog.md --date 2026-03-14
```

Week form:

```bash
uv run changelog_digest.py examples/sample-changelog.md --date 2026-03-14 --week
```

Output folder form:

```bash
uv run changelog_digest.py examples/sample-changelog.md --date 2026-03-14 --out-dir digests
```

## Options

| Option | What it does |
| --- | --- |
| `changelog` | Path to the markdown changelog. Required. |
| `--date YYYY-MM-DD` | Day to digest. Defaults to the local date today. |
| `--week` | Digest the seven days ending on `--date`, including both ends. |
| `--dry-run` | Print the prompt and do not call a model. |
| `--out-dir DIR` | Also save the digest as `DIR/YYYY-MM-DD-digest.md`, or `DIR/YYYY-MM-DD-weekly-digest.md` with `--week`. The directory is created if needed. |
| `--model MODEL` | Model id. Defaults to `claude-sonnet-5-5`. |
| `--effort LEVEL` | How much the model thinks: `low`, `medium`, `high`, `xhigh` or `max`. Defaults to `low` with the default model. With another model it is sent only when you pass it. |

`--out-dir` replaces an existing digest file for the same day. A digest cut off at the token limit is saved with a last line saying so. The 16,000 token limit covers the model's thinking as well as the digest text.

## What is sent to the model

A daily digest sends every inline field and every `###` section of the day's entries except Files Modified. A weekly digest sends only the date, the session, the title, What Changed, and Breakthroughs. Nothing else in the file is sent.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | The digest was printed, or there was nothing to do. |
| 1 | The run could not finish. |
| 2 | The command line was not valid. |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests use only the standard library and run on Python 3.9 or newer.

## The story (then and now)

**Then.** On December 4, 2025, my changelog was growing every session, and I asked: "what are we doing to nurture this infinite beast ALL-PROJECTS-MASTER? ... please generate daily digests for the lats 5 days so i can see how well they are done." By that afternoon it had four layers (a database table, a monthly archive, a per-project file trimmed to 15 entries, a master timeline trimmed to 20) and a 245-line script that turned a day of entries into a hook, a story and video ideas. The note on that entry: "changelogs can be CONTENT, not just logs." The next morning I said why: I was starting a studio, and "I need a lot of content." The script read from a hosted Postgres database and pip-installed its own packages when they were missing. Its digests stop on December 4, 2025.

**Now.** The changelog is still written; its newest entry is from October 13, 2026. The digest script never went on a schedule. This repo is a rewrite anyone can run: it reads a plain markdown changelog, has no database and installs nothing by itself, ships one made-up sample changelog, and `--dry-run` prints the exact prompt without a key. 56 tests, standard library only.

State: the changelog habit runs in my setup; this digest tool is a rewrite, early. Point it at your own changelog with `--dry-run` and see what it would ask.

## License

MIT. See LICENSE.
