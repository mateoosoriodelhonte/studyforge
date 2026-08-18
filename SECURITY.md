# Security

## What StudyForge is, in security terms

StudyForge is a **single-user, local-first desktop-style web application**. It
is designed to be run by one person on their own machine, bound to
`127.0.0.1`, holding their own study material.

**It has no authentication, and that is a deliberate design decision, not an
omission.** Adding a login to a single-user local application would be
security theatre: it would protect your notes from you.

This document describes the threat model that follows from that, and the
protections that are actually implemented. **StudyForge is not described here
as "secure"** — that word means nothing without a threat model. What follows is
a list of specific things it does, and specific things it does not.

## Threat model

### In scope

| Threat | Why it matters |
|---|---|
| **Malicious uploaded files** | You may upload a PDF you did not write. It is parsed by a library, on your machine. |
| **Malicious content inside documents** | Extracted text is rendered back to you. A crafted document must not be able to inject script. |
| **Path traversal via filenames** | A filename is attacker-controlled input. |
| **Injection through search** | Search input reaches a query engine. |
| **Untrusted model output** | An AI provider is a remote service returning strings. |
| **Information disclosure in errors** | An error page must not leak paths, versions or stack traces. |
| **Accidental secret logging** | Logs must not contain keys or private documents. |

### Out of scope

| Not defended against | Why |
|---|---|
| Another user on the same machine | A local user with your account can read `./data/` directly. This is true of any local application. |
| Exposure on an untrusted network | There is no authentication. Do not bind StudyForge to a public interface. See [Deployment](docs/DEPLOYMENT.md). |
| Malicious local code | If something can run as you, it can read your files regardless. |
| Full disk encryption | The database is a plain SQLite file. Use your operating system's disk encryption. |

## Protections implemented

### Uploads

- **Size limit**, enforced *while streaming* — an oversized upload is rejected
  without being buffered in memory. Default 20 MB, configurable.
- **Allow-list of file types** (`.txt`, `.md`, `.pdf`). There is no
  "unknown but probably fine" path.
- **Three independent checks**: extension, declared content type, and **magic
  bytes**. The magic-byte check decides, because the other two are
  attacker-controlled.
- **Executables and archives are refused by signature** regardless of what they
  are named — `MZ`, `ELF`, Mach-O, `PK`, gzip, RAR, `#!`.
- **Text files must decode**, and are rejected if they contain NUL bytes.
- **Validation runs before anything is written to disk**, so a rejected upload
  leaves no trace.
- Stored files are written `0600`.

### Path traversal

Prevented **by construction, not by filtering**. Stored filenames are a UUID
plus an extension drawn from the validated allow-list, so **no byte of user
input ever reaches a path**. The user's filename is display metadata only.

As an independent second check, every resolved path is verified to sit inside
the uploads directory — which also catches a symlink planted in the store.

### Rendering

- Jinja2 autoescaping is on for every template.
- There are exactly two places that construct markup, both in
  `web/templating.py` for the search highlighter, and both escape their input
  first and add only `<mark>` tags. Each carries a comment saying so.
- No user or model content is ever rendered raw.
- Tested with eight XSS payloads through course names, document titles and
  bodies, both flashcard sides, filenames, and the search highlighter.

### SQL

- All persistence goes through SQLAlchemy's expression language or bound
  parameters. There is no string-interpolated SQL anywhere in the query path.
- **FTS5 queries never receive raw user input.** `to_match_query` strips every
  FTS operator and quotes each remaining term; an unbalanced quote or a `NEAR(`
  cannot reach the parser.
- The one module that interpolates identifiers into SQL (`fts.py`, creating the
  index tables) does so only from a module-level constant, is documented as
  such, and takes no input.

### Errors

- Custom 404 and 500 pages. **No stack trace is ever sent to a user.**
- In `ENVIRONMENT=production`, 500s show a fixed message; the detail goes to
  the log only.
- Service errors carry messages written for a person, and a test asserts none
  contains `Traceback`, an absolute path, a library name or a pointer.

### Logging

- Structured events with identifiers and counts, to stderr.
- String values truncated at 200 characters, so a stray field cannot copy your
  notes into a log.
- No secrets are logged. `SECRET_KEY` and any token are never included.

### AI

- All structured output validated with Pydantic before use.
- Output that is schema-valid but incoherent is dropped.
- A provider can never influence scheduling, build SQL or a path, make an
  authorisation decision, or have its output rendered as HTML.
- Prompts carry only the minimum passages needed — asserted by a test.

### Dependencies

- `pip-audit` runs in CI on every push and pull request against the resolved
  runtime dependency set.
- Dependabot proposes updates weekly, grouping patch and minor versions and
  isolating majors so they get read rather than rubber-stamped.
- The dependency list is short and deliberately so.

## Known limitations

Stated plainly, because a security document that lists only strengths is
marketing:

- **No authentication.** By design, for a single-user local application. It
  makes StudyForge unsuitable for shared hosting without a reverse proxy that
  handles authentication itself.
- **No CSRF tokens.** There are no cross-origin state-changing requests to
  protect, and no authenticated session to ride: with no login, a forged
  request achieves nothing an attacker could not do by opening the app. If
  authentication is ever added, **CSRF protection must be added with it.**
- **No rate limiting.** Irrelevant for a local single-user app; it would matter
  if hosted.
- **PDF parsing runs in-process.** `pypdf` is pure Python with no C
  dependency, which removes a whole class of memory-safety bugs, but a
  malicious PDF could still consume CPU. Page count is capped at 2,000 and
  extraction failures are caught, but there is no sandbox or hard timeout.
- **The database is not encrypted.** Use full-disk encryption.
- **`SECRET_KEY` has an insecure default** so that a fresh clone runs with no
  configuration. It signs only a flash-message cookie. The application logs a
  warning if it is left at the default in `ENVIRONMENT=production`.

## Reporting a vulnerability

Please open a
[private security advisory](https://github.com/mateoosoriodelhonte/studyforge/security/advisories/new)
rather than a public issue.

This is a personal open-source project with no service level agreement. I will
respond as promptly as I reasonably can and will credit reporters who want it.
