# File organizer

Sorts a directory into category subfolders by file type, with an asyncio engine,
bounded concurrency, SHA-256 duplicate detection and a JSON activity log.

```bash
organizer ~/datheon-demo --dry-run --duplicates
```

## The session

A folder of eight files — documents, an image, audio, a video, code, an archive,
and a second copy of a PDF — sorted in **dry-run** mode, so the plan is shown and
nothing moves.

The session behind the sweep above is committed, not typed: [`assets/demos/organizer.json`](../assets/demos/organizer.json) is the exact file the player fetches, and `python tools/live_demos.py --check` fails if this page and that file ever disagree.

<!-- live-demo:begin -->
<div class="demo-terminal" data-session="assets/demos/organizer.json" data-command="organizer ~/datheon-demo --dry-run --duplicates">
<pre class="demo-terminal__fallback" aria-label="Captured session: A messy folder, sorted (dry run)">$ organizer ~/datheon-demo --dry-run --duplicates
Organising: ~/datheon-demo   [DRY RUN (no changes made)]
------------------------------------------------------------
 ~&gt; archive.zip  =&gt;  Archives/
 ~&gt; clip.mp4  =&gt;  Videos/
 ~&gt; notes.txt  =&gt;  Documents/
 ~&gt; photo.jpg  =&gt;  Images/
 ~&gt; report-copy.pdf  =&gt;  Documents/
 ~&gt; report.pdf  =&gt;  Documents/
 ~&gt; script.py  =&gt;  Code/
 ~&gt; song.mp3  =&gt;  Audio/
------------------------------------------------------------
Processed : 8
Moved     : 0
Planned   : 8
Errors    : 0
Duration  : 2.4 ms (asyncio engine)

Duplicate groups found: 1
  ac106884df28…  x2
     - ~/datheon-demo/report-copy.pdf
     - ~/datheon-demo/report.pdf</pre>
</div>
<!-- live-demo:end -->

## What to look at

- **Dry run is the same code path.** The engine decides where every file goes and
  then either performs the move or records it as planned — the plan you see is
  the plan it would execute, not a separate preview implementation.
- **Moves run on the asyncio engine**, at most `--concurrency` in flight (8 by
  default, 64 maximum). Filesystem work is latency-bound, so a modest fan-out is
  enough to keep the disk busy without thrashing it.
- **Collisions are renamed, never overwritten.** A second `report.pdf` becomes
  `report (1).pdf` rather than replacing the first — an organiser that loses
  files is worse than an unsorted folder.
- **Duplicates are content, not names.** `report.pdf` and `report-copy.pdf` hash
  to the same SHA-256 and are reported as a group; the digest is truncated for
  display, the comparison is not.
- **The duration line is honest about the engine.** `Duration : 2.4 ms (asyncio
  engine)` is measured around the whole sweep, so a regression is visible in the
  transcript.

## Run it yourself

```bash
organizer ~/Downloads --dry-run                 # what would happen
organizer ~/Downloads                           # do it
organizer ~/Downloads --duplicates --log run.json
organizer ~/Downloads --concurrency 4           # bound the fan-out
organizer ~/Downloads --json                    # one machine-readable document
```

From a checkout: `python projects/03_file_organizer_automation/main.py ~/Downloads --dry-run`.

Two guards worth knowing: a subfolder the organizer itself created is never
re-sorted, and a file it cannot read is reported with its path instead of
aborting the sweep.

## How it is built

| Module | What lives there |
| --- | --- |
| `organizer.py` | Category mapping, `OrganizeReport`, the synchronous facade, the JSON log |
| `async_organizer.py` | The asyncio engine: bounded semaphore, per-file tasks, deterministic result order |
| `main.py` | The CLI and the report renderer |
| `benchmark.py` | The harness behind the Day 30 benchmark budget |

The synchronous API is a wrapper, not a second implementation: `organize_sync`
runs the same coroutine on a private loop when the caller already has one.

## Under the gates

- `tests/test_organizer.py` — 27 tests: category mapping, collisions, dry run,
  permission errors, the JSON log.
- `tests/test_async_organizer.py` — 36 tests: bounded concurrency, deterministic
  ordering, cancellation, a fake mover so no test touches the real filesystem.
- The engine is one of the four hot paths timed by `benchmarks/bench_core.py`,
  with a committed ceiling in `benchmarks/budget.json`.

See [async I/O](../async_io.md), [persistence](../persistence.md) and the
[API reference](../api/projects.md).
