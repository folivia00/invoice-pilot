# Architecture

invoice-pilot turns an invoice file into validated fields and a verdict: `auto_approved`
or `needs_review`. The model reads the document; plain Python code decides whether the
result can be trusted.

## Modules

| Package | Role | I/O |
|---|---|---|
| `domain/` | Invoice schema (pydantic, `Decimal` amounts), deterministic checks, issue codes, IBAN and tax ID rules, currency minor units | none: pure functions |
| `extraction/` | File signature (PDF, JPEG, PNG), size and page limits, PDF text extraction with pypdf | none |
| `llm/` | One provider interface for Gemini and OpenAI, the versioned prompt and response schema, HTTP timeouts and retries, prices, recording and replay of answers | HTTP to the model API |
| `services/` | Processing pipeline, daily limits and LLM budget, queue, rate limiter, export, retention, statistics, the fake model | through `storage` and `llm` |
| `storage/` | SQLite through SQLAlchemy (async); documents, model calls, users | database |
| `bot/` | aiogram handlers: parse the update, call a service, format the reply | Telegram |
| `api/` | aiohttp HTTP API: `GET /health`, `POST /extract` | HTTP |

Handlers in `bot/` and `api/` hold no business logic. Admin rights are decided by the
service layer from the actor's ID. Configuration is one settings module (`config.py`);
the Compact and Full profiles are switched by `PROFILE`, not by code branches.

## Data flow

1. **Metadata check.** Type and size come from the Telegram metadata (or the HTTP
   upload) before the file is downloaded or read.
2. **Daily slot.** `DailyLimits` reserves a slot: per user, for all users, and within the
   daily LLM budget in USD.
3. **Queue.** The upload waits with its metadata only. Compact processes one document at
   a time, and a full queue refuses new uploads (`QUEUE_MAX`).
4. **Download.** Once the document holds a processing slot, the bytes are downloaded into
   memory. They are never written to disk.
5. **Prepare.** The signature, size and page count are checked. A PDF with a text layer is
   sent as extracted text; a scan or a photo is sent as the original bytes (base64, once).
   Nothing is rendered or resized locally.
6. **Model call.** The rate limiter takes a slot for every HTTP attempt, retries
   included. The HTTP layer retries 408, 429 and 5xx up to 3 attempts with exponential
   backoff and honours `Retry-After`. A spend-limit error is not retried.
7. **Parse.** The JSON answer is parsed with numbers kept as `Decimal`, never `float`.
   An answer that does not fit the schema gets one more model call; a second failure
   means `needs_review` with `extraction_failed`.
8. **Validate.** Deterministic rules produce issues; an error-level issue means
   `needs_review`.
9. **Store and reply.** Only the extracted fields and call metadata (model, tokens,
   latency, status, cost) are stored. The file bytes are dropped. One log line is written
   per document.
10. **Retention.** A job inside the process deletes fields, call metadata and idle users
    older than `RETENTION_DAYS` every day at 05:00 UTC.

## Key decisions

### Rules on top of the LLM

The model is good at reading and bad at knowing when it is wrong. So it only returns
fields in a strict schema and never decides the verdict. The checks are plain code with
tests: sums, tax, dates, IBAN check digits, VAT ID and EIN formats, currency codes. A
document is auto-approved only when the printed numbers agree with each other. The
measured effect (documents with every key field right from the model alone, precision
among auto-approved documents, corrupted documents caught) is in the
[Results](../README.md#results) section of the README, generated from the evaluation
results; the methodology is in [evaluation.md](evaluation.md).

### Tolerance only for rounding

- Sums of amounts that are already rounded must match exactly: sum of lines = subtotal,
  subtotal + tax = total. A one-cent difference is an extraction error.
- A computed value compared with a printed rounded value (quantity × unit price, subtotal
  × rate) may differ by half a minor unit of the currency (EUR 0.005, JPY 0.5). That is
  the maximum error of round-half-up, so a correctly rounded amount always passes and a
  one-unit error never does.
- The constants are named in `domain/constants.py`.

### Memory and garbage collection

The demo runs within a 256 MiB service limit (200 MiB soft limit), with no swap.

- **One copy of the bytes.** The file is kept in memory once, base64-encoded once, and
  every reference is released after the model call.
- **gc after each document.** httpx request objects form reference cycles that keep each
  request body (~7 MB for a 5 MB scan) alive until the cyclic collector runs. Without a
  collection, the bodies of several large documents pile up. The processor runs
  `gc.collect()` when a document leaves its slot.
- **glibc mmap threshold.** The start script sets `MALLOC_MMAP_THRESHOLD_=131072`, so
  large buffers are returned to the system when freed.
- **Lazy imports.** pypdf and openpyxl are imported only when needed. A test checks that
  importing the bot does not load alembic, openpyxl, PIL or pypdf. Migrations run as a
  separate process before the bot starts.

Measured: 135.7 MiB at rest and 170.0 MiB peak for a 5 MB scan in the benchmark container
(256 MB, 0.5 CPU; [bench/README.md](../bench/README.md)).

### A queue without bytes

Waiting uploads hold only Telegram metadata. The file is downloaded when the upload gets
its processing slot, so 10 waiting scans cost no memory, and the user sees its position
("in queue").

### Limits and races

aiogram handles updates concurrently, so two uploads of one user can arrive at the same
time. Each check-and-reserve runs under the user's lock, then the global lock, inside
one database transaction, so both uploads cannot see "1 left" and both pass. A
reservation counts from that moment. Input errors and provider failures give the slot
back. Reservations left by a crash are marked failed at startup. The locks are
in-process, so each database is used by exactly one process; the bot and the HTTP API
have separate databases.

### Recordings and replay

Every model answer used in the evaluation is recorded with a key built from the document
digest, provider, model, prompt version, schema digest and model parameters. Replays are
deterministic and need no keys, so CI recomputes every published metric on each push.
