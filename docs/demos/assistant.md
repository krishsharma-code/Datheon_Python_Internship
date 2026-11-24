# Terminal assistant

A rule-based assistant that matches what you type against intents compiled from
a JSON dataset, answers with the response it picked, and shows the intent and
confidence behind every reply.

```bash
assistant --no-log
```

## The session

Three turns — business hours, refund policy, goodbye — against the bundled
`intents.json`.

The session behind the three turns above is committed, not typed: [`assets/demos/assistant.json`](../assets/demos/assistant.json) is the exact file the player fetches, and `python tools/live_demos.py --check` fails if this page and that file ever disagree.

<!-- live-demo:begin -->
<div class="demo-terminal" data-session="assets/demos/assistant.json" data-command="assistant --no-log">
<pre class="demo-terminal__fallback" aria-label="Captured session: Three turns with the intent matcher">$ assistant --no-log
  ____        _   _   _
 |  _ \  __ _| |_| | | | ___  ___  _ __
 | | | |/ _` | __| |_| |/ _ \/ _ \| '_ \
 | |_| | (_| | |_|  _  |  __/ (_) | | | |
 |____/ \__,_|\__|_| |_|\___|\___/|_| |_|
 Rule-Based Terminal Assistant — Datheon Task 4

Type a message, or 'bye'/'exit'/'quit' to finish.
you &gt; what are your hours
bot &gt; We're open Monday to Friday, 9:00 AM to 6:00 PM IST. We're closed on weekends.  [business_hours · 50%]
you &gt; refund policy
bot &gt; We offer a full refund within 14 days of purchase — no questions asked.  [refund · 50%]
you &gt; exit
bot &gt; Goodbye! Have a great day. 👋  [exit · 100%]</pre>
</div>
<!-- live-demo:end -->

## What to look at

- **Every reply names its intent and confidence.** `[business_hours · 50%]` is
  not decoration: it is the score the matcher computed, which is what makes a
  wrong answer debuggable instead of mysterious.
- **Matching is keyword-based, on purpose.** Each intent compiles a regular
  expression from its keywords; the assistant is deterministic, offline and
  inspectable — no model, no network, no API key.
- **Exit commands are data, not code.** `bye`, `exit` and `quit` come from the
  dataset's `exit_commands`, so the vocabulary can change without touching the
  program.
- **The conversation log is optional.** `--log conversation.jsonl` appends one
  JSON object per turn through an atomic append; `--no-log` writes nothing.
  A full disk disables logging for the rest of the session instead of ending it.

## Run it yourself

```bash
assistant                                   # the bundled intents
assistant --faq projects/04_terminal_assistant/sample_faq.json
assistant --log conversation.jsonl          # keep a transcript
assistant --validate                        # check the dataset and exit
assistant --json                            # one machine-readable document
```

From a checkout: `python projects/04_terminal_assistant/main.py`.

A malformed dataset is a hard error with the offending entry named —
`intents[3].patterns[0]` — and exit code `1`, rather than a session that answers
nothing.

## How it is built

| Module | What lives there |
| --- | --- |
| `chatbot.py` | `Intent`, `IntentEngine`, `ConversationLogger`, the FAQ loader |
| `main.py` | The CLI and the REPL |

Compiled patterns go through `caching.compile_pattern`, so two intents that share
a regular expression compile it once and `cache_stats()` can show it. The dataset
itself is read through `dataset_loader`, which is where the BOM, CRLF and
encoding tolerance lives.

## Under the gates

- `tests/test_chatbot.py` — 33 tests: intent parsing, keyword extraction,
  confidence scoring, exit commands, malformed datasets, the log.
- The intent loader was one of the two defects Day 18's fuzzer found, and its
  fixes are pinned by regression tests.
- Day 31's type gate caught a third: a JSON **string** where a list was expected
  was iterated character by character. `_string_list` now validates it, and the
  case is tested.

See [caching](../caching.md), [CLI contract & exit codes](../cli_contract.md) and
the [API reference](../api/projects.md).
