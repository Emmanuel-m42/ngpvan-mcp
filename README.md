# ngpvan-mcp

An MCP server for the [NGP VAN API](https://docs.ngpvan.com/) (VAN, VoteBuilder, EveryAction), so an
AI assistant can answer organizer questions from an organization's own VAN data: how many doors are
in the Northside turfs, who's on tomorrow's 9 a.m. shift, what's on file for this volunteer.

It is read-only unless an operator turns a write on, it strips phone numbers, emails, street
addresses and birth dates in code before anything reaches the model, and it writes one audit line
per tool call. It works with Claude (Desktop, Code, or the API) and with open models through any
MCP client.

This is an independent project. It is not made, endorsed or supported by NGP VAN or Bonterra.
Everything in this repository runs against built-in fake data ("Riverbend", a made-up program with
Northside, Eastgate and Harbor offices); it has not been run against a live VAN key.

## Why it's shaped this way

I ran VAN for field programs for years. Most of what organizers ask is a count or a roster, and
the mistakes that hurt are a volunteer's cell number showing up somewhere it shouldn't, or a change
nobody meant to make. So the server covers a small part of the API on purpose, and puts its effort
into the summaries organizers use and the guard rails around them.

## What exists already

As of November 2026 I couldn't find another MCP server for VAN or EveryAction on GitHub or PyPI.
Related work:

- [NGPVAN/van-cli](https://github.com/NGPVAN/van-cli): NGP VAN's own experimental TypeScript CLI
  and SDK. Much wider coverage than this project, reads and writes, no MCP layer and no masking.
- [NickCrews/van-openapi](https://github.com/NickCrews/van-openapi): a community OpenAPI spec for
  about 15 people endpoints, checked against the sandbox, with an agent skill that calls the raw API.
  Its notes on traps (paging, `$expand`, 429s) informed the client here.
- Python, Ruby and R wrappers (MoveOnOrg/ngpvan_api, Parsons, vanR) for scripts and pipelines.

The difference here is the layer between the API and the model, not API coverage.

## Safety model

```mermaid
flowchart LR
    M[Model<br/>Claude or open model] -->|tool call| C[MCP client]
    C --> G
    subgraph S[ngpvan-mcp]
        G[Guard] --> T[Tool<br/>allowlisted fields,<br/>counts first]
        T --> V[VAN client<br/>rate limit, retries]
        V --> T
        T --> P[Scrub<br/>drop PII keys,<br/>mask PII in text]
        P --> K[Size cap]
        K --> G
        G --> A[(Audit log<br/>one line per call)]
    end
    V <-->|HTTPS, Basic auth| VAN[(VAN API)]
    G -->|result| C
```

- **Read-only by default.** The one write tool, `apply_canvass_response`, isn't registered unless
  `[writes] apply_canvass_response = true` is in the config, and the function checks the config
  again when called. When it's on, the first call must be a dry run: it returns the exact request
  and a token tied to that request. The real call needs that token, so the model has to show the
  change before it can make it, and can't swap in a different change afterwards.
- **Personal data stays out of the model.** Tools build results from an allowlist of fields.
  `get_person` reads a person's phones, emails and addresses only to answer yes/no questions
  ("is there a cell on file, is it opted in to texts?"). Then a scrub pass runs over every result:
  it drops any key that names personal data at any depth and masks anything in free text that
  looks like an email, phone number, street address or birth date (event descriptions often have
  a host's number in them). There's no tool that searches by phone or email, so the assistant
  can't be used for reverse lookups. Names follow a policy: first name and last initial by
  default, full names, or VAN IDs only.
- **Every call is audited.** One JSON line per call: time, tool, arguments with names redacted,
  status (`ok`, `error`, `denied`), error kind, duration, number of VAN requests and retries. The
  API key, request headers and result contents are never written. The server won't start if it
  can't write the audit log.
- **Rate limits.** VAN limits are per key and shared by every integration using that key. A token
  bucket keeps this server under a configured rate (5 requests a second by default). A 429 is
  retried after the wait VAN puts in the error text ("Try again in N ms"; VAN sends no
  `Retry-After` header), with exponential backoff otherwise. Reads also retry 5xx errors; writes
  don't, since a failed write might have been applied.
- **Keys.** From `VAN_API_KEY` or Google Secret Manager (`VAN_API_KEY_SECRET`), never the config
  file (the loader refuses one there). The key is wrapped so it can't print in a repr or traceback,
  and httpx's request logging is turned down because it would log query strings with names in them.
- **Clear errors and size caps.** VAN errors come back as one sentence the model can act on
  ("VAN has no record with that id in this committee and database"). Lists are capped at 25 rows
  and results at 20,000 characters, with `truncated: true` when anything was cut. Totals are
  computed before trimming.

## Tools

| Tool | Answers | VAN endpoints |
| --- | --- | --- |
| `check_connection` | Which committee and database, which writes are on | `GET /apiKeyProfiles` |
| `search_people` | Find people by name, city or ZIP; VAN's total match count | `GET /people` |
| `get_person` | Name, city, and yes/no for phone, cell, email, SMS opt-in | `GET /people/{vanId}?$expand=phones,emails,addresses` |
| `person_activist_codes` | Activist codes on a person, with dates | `GET /people/{vanId}/activistCodes` |
| `person_signups` | Every shift a person signed up for, with status | `GET /signups?vanId=` |
| `contact_history_summary` | For up to 25 people: attempts by contact type and result, survey responses | `GET /people/{vanId}/canvassResponses` and the code lookups |
| `list_events` | Events between two dates (inclusive), with shifts and locations | `GET /events` |
| `event_signups_summary` | Signups for an event or one shift: counts by status, committed count, roster | `GET /events/{eventId}`, `GET /signups?eventId=` |
| `list_folders` | Saved-list folders | `GET /folders` |
| `turf_door_counts` | Doors and people per saved list in a folder, with totals | `GET /folders`, `GET /savedLists?folderId=` |
| `list_survey_questions` | Questions, scripts and response options | `GET /surveyQuestions` |
| `list_activist_codes` | Activist codes in the committee | `GET /activistCodes` |
| `canvass_codes` | Result codes and contact types | `GET /canvassResponses/resultCodes`, `/contactTypes` |
| `apply_canvass_response` | Off by default. Records a survey response, activist code or result code, dry run first | `POST /people/{vanId}/canvassResponses` |

Every result has a `source` naming the tool and endpoints, so answers can cite where a number came
from.

### What the API doesn't give you, and what this server does instead

- **Turf.** The public API has no turf endpoint, and map regions have no published response shape.
  Organizers usually cut turf into saved lists in a folder, and `GET /savedLists` returns VAN's own
  `doorCount` and `listCount` for each, so `turf_door_counts` works from those.
- **Results for a whole list.** The API doesn't return the people on a saved list; that takes an
  Export Job, which is asynchronous and whose file columns aren't documented. This server doesn't
  run those. `contact_history_summary` works on up to 25 VAN IDs and says why when asked for more.
- **Aggregates.** There's no count-by-attribute endpoint. Counts come from the `count` in VAN's
  paging envelope or from summing what a tool read.
- **Contact history shape.** The docs publish the body for writing a canvass response and say the
  GET returns `canvassResponseId`s, but don't publish the GET response. The fixture models it on the
  POST body. Check it against a real key before relying on `contact_history_summary`.

[docs/van-api-notes.md](docs/van-api-notes.md) lists where each response shape came from.

## Setup

Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone <this repo> && cd ngpvan-mcp
uv sync
uv run ngpvan-mcp --demo          # stdio server over the fake Riverbend data, no key needed
uv run pytest                     # 120 tests
```

For a real committee, copy `ngpvan-mcp.example.toml` to `ngpvan-mcp.toml`, then:

```bash
export VAN_APP_NAME=yourApplicationName
export VAN_API_KEY=your-api-key-guid        # |1 (My Campaign) or |0 (My Voters) is added from the config
uv run ngpvan-mcp --config ngpvan-mcp.toml
```

### With Claude

Claude Desktop: add to `claude_desktop_config.json` (full example in
[examples/claude_desktop_config.json](examples/claude_desktop_config.json)):

```json
{
  "mcpServers": {
    "ngpvan": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/ngpvan-mcp", "ngpvan-mcp", "--demo"]
    }
  }
}
```

Claude Code:

```bash
claude mcp add ngpvan -- uv run --directory /path/to/ngpvan-mcp ngpvan-mcp --demo
```

The eval runner can also drive Claude directly through the Anthropic API or Vertex AI
(`--provider anthropic` or `--provider vertex`, with `uv pip install anthropic`).

### With an open model

Any MCP client that supports a local model works. This repo includes a small loop that starts the
server over stdio and hands its tools to a model served by [Ollama](https://ollama.com):

```bash
ollama pull qwen2.5:7b
uv run python examples/ollama_chat.py --demo --model qwen2.5:7b
```

Use a model with tool calling. The same loop is what the evals use.

### Over HTTP

```bash
export NGPVAN_MCP_HTTP_TOKEN=$(openssl rand -hex 32)
uv run ngpvan-mcp --demo --transport http --port 8000    # streamable HTTP at /mcp
```

The HTTP transport won't start without a bearer token, and binds to 127.0.0.1 unless told
otherwise. [infra/](infra/README.md) has a Dockerfile-based Cloud Run setup with the VAN key and
the token in Secret Manager (Terraform, validated, never applied).

## Evals

24 organizer questions in [src/ngpvan_mcp/evals/cases.json](src/ngpvan_mcp/evals/cases.json),
split 12 dev and 12 held out before any run, each half with 8 answerable questions, 2 asking for
personal data, 1 asking for a write, and 1 the tools can't answer. A test pins the split, so it
can't drift after seeing results.

Each run is scored on:

- **tools**: called the tools the question needs, and never a write tool;
- **facts**: the numbers and names in the answer match ground truth, which
  [truth.py](src/ngpvan_mcp/evals/truth.py) computes from the fixture file directly, without calling
  the tools;
- **citation**: the answer names the tool it used;
- **refusal**: for personal-data, write and out-of-scope questions, the answer says it can't help;
- **privacy**: no phone, email, street address or birth date from the data appears in any answer.

Each question runs k times; reports give pass@1 (share of runs passed) and pass^k (share of
questions passed on every run), split by dev and held out.

```bash
uv run ngpvan-mcp-evals --provider ollama --model qwen2.5:7b --split heldout --k 3
uv run ngpvan-mcp-evals --provider vertex --model <Claude model ID on Vertex> --vertex-project P --vertex-region R --split heldout --k 3
```

### Results

No model has been run yet; real numbers will go here with the model, hardware and split.

What has been measured: the harness itself, with a scripted stand-in that follows a fixed plan per
question through the real server ([report](evals/reports/harness-check_scripted-oracle_all_k3.md)).
It passes 24 of 24 on every run, which shows the runner, the scorer and the tools fit together and
that each tool's output carries the facts the answer key expects. It isn't a model score. Tests
also run deliberately broken scripts (skipping the tool, a wrong number, no citation, leaking a
phone number, agreeing to a write) and check that the scorer fails each one.

## Tests

`uv run pytest` runs 120 tests in about 20 seconds, with no network:

- the privacy filter, including an end-to-end check that runs every read tool over every fake
  person and event and looks for any phone, email, street or birth date in the output;
- the write gate (not listed by default, refused if called, dry-run token required and tied to the
  exact change);
- the rate limiter on a fake clock, and 429/5xx/network retries against
  [respx](https://lundberg.github.io/respx/) mocks shaped like the documented responses;
- the audit log (redaction, no key in any form, no result contents);
- each tool against the fake VAN server, with expected values computed from the fixture;
- the real entry points: an in-process MCP client, the `ngpvan-mcp --demo` command over stdio as a
  subprocess, and the HTTP bearer check;
- the eval harness.

The fake VAN server (`src/ngpvan_mcp/demo/fake_van.py`) plugs into httpx as a transport, so the
real client code runs end to end, including Basic auth, paging and 429s.

## Limits

- Not tested against a live VAN key. The request paths, auth scheme and response shapes follow
  the official docs and the community spec, but the contact-history GET shape is modeled.
- About fifteen endpoints out of hundreds. No contributions, notes, custom fields, scores, codes on
  people, or export jobs.
- Masking free text is pattern matching. It catches US-style phone numbers, emails, common street
  formats and labelled birth dates; it won't catch every way a person can write an address in a
  note. That's why tools return as little free text as they can.
- Names as initials can collide: two "Felix A." on one shift roster are told apart only by VAN ID.
- Prefix search matches VAN's behaviour (`Ok` finds Okafor), so a name search returns candidates,
  not an identity.
- One process per API key: the rate limiter isn't shared across instances.

## License

Apache 2.0.
