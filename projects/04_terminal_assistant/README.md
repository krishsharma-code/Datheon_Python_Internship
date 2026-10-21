# Task 4 — Rule-Based Interactive Terminal Assistant

A conversational terminal assistant with multi-intent recognition, built on
`re` and `json` from the standard library — no ML model, just transparent
pattern matching.

## Features

- **Regex-driven intent recognition** with per-intent pattern scoring.
- **Confidence scoring** — each reply reports a 0.0–1.0 match confidence.
- **Multi-intent matching** — an intent fires when *any* of its patterns match;
  more matches raise confidence, and ties break on intent priority.
- **Graceful fallbacks** — rotating responses when nothing matches.
- **Clean exit commands** — `bye`, `exit`, `quit`, `goodbye`, `stop`.
- **Persistent conversation logging** — every turn is appended to
  `conversation.jsonl` with a UTC timestamp, intent, and confidence.
- **JSON-driven dataset** — edit `intents.json` to add intents without touching
  code.

## Run

```bash
python projects/04_terminal_assistant/main.py
python projects/04_terminal_assistant/main.py --no-log   # skip logging
python projects/04_terminal_assistant/main.py --intents my_intents.json
```

## Example session

```text
you > hello
bot > Hello! I'm the Datheon support assistant. How can I help you today?  [greeting · 50%]
you > what are your hours?
bot > We're open Monday to Friday, 9:00 AM to 6:00 PM IST.  [business_hours · 50%]
you > bye
bot > Goodbye! Have a great day. 👋  [exit · 100%]
```

## Architecture

| File | Responsibility |
| --- | --- |
| `chatbot.py` | `IntentEngine`, `Intent`, `Response`, `ConversationLogger`, `normalize`. |
| `intents.json` | Intent patterns, responses, fallbacks, exit commands. |
| `main.py` | REPL, banner, logging wiring. |

## Adding an intent

Add an entry to the `intents` array in `intents.json`:

```json
{
  "name": "shipping",
  "patterns": ["\\b(ship|delivery|deliver)\\b"],
  "responses": ["We ship within 3–5 business days across India."]
}
```
