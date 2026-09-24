# LearnWithAI architecture

## Purpose and design choice

LearnWithAI begins as a single, dependency-free Python application so an NGO or delivery partner can run a real pilot without managing a complex platform. The browser application and JSON API share one origin, while SQLite makes the first deployment portable and easy to back up.

The design intentionally separates the delivery surface from the long-term production topology. The same product boundaries work when SQLite is replaced by a managed relational database and video delivery moves to dedicated infrastructure.

## System context

```text
Learner or administrator browser
        |
        | HTTPS in production / HTTP locally
        v
Reverse proxy or load balancer (production)
        |
        v
LearnWithAI Python process
  |-- serves web/ static application
  |-- authentication and authorisation
  |-- course, lesson, enrolment, progress, quiz, upload, and tutor APIs
  |-- SQLite data access
  |-- private uploaded-media streaming
        |                    |                         \
        v                    v                          v
SQLite database       data/uploads/                Gemini API
data/learnwithai.db   private pilot media          temporary processing files

Object storage/CDN should replace local uploaded media in production; large
media files never live in the application repository or SQLite.
```

## Application boundary

`server.py` is the application entry point. It serves `web/` and exposes the backend capabilities through the same origin:

- Account registration, login, logout, and current-user lookups using bearer authentication.
- Learner access to courses and lessons, enrolment, lesson progress, quizzes, protected uploaded-video playback, and the tutor experience.
- Administrator-only course and lesson management, video upload, and transcript selection.
- A local tutor fallback when no session-only Gemini key is configured.

The server auto-creates its `data/` directory and SQLite schema for local use. Production should place the database on a persistent, access-controlled volume and use a backup strategy that has been tested by restoring it.

## Identity and authorisation

There are two initial roles:

| Role | Primary capability |
| --- | --- |
| `learner` | Discover learning content, enrol, track progress, complete quizzes, and ask for learning support. |
| `admin` | Manage course and lesson content in addition to learner capabilities. |

Passwords are protected with PBKDF2-HMAC. The browser sends bearer tokens to authenticated API endpoints; the server stores hashes of those tokens and enforces expiry. APIs validate incoming data and use parameterized SQLite queries, which keeps untrusted request values separate from SQL instructions.

The static app and API share an origin. This avoids granting a broad cross-origin browser API surface and keeps authentication flows simpler. A native video tag cannot attach the bearer header, so LearnWithAI issues a five-minute, lesson- and user-scoped opaque playback ticket as an `HttpOnly`, `SameSite=Strict` cookie whose path is restricted to the lesson stream. The ticket is held only in memory, is cleared on logout, and is not an API bearer token. Set `LEARNWITHAI_COOKIE_SECURE=1` behind HTTPS.

A Gemini key is submitted over the authenticated same-origin session, held only in backend memory against the hash of that session token, and cleared on key removal, logout, token expiry, or server restart. The key is never written to SQLite, returned by an API, placed in a URL, or logged.

## Data and media

SQLite is the default source of truth for accounts, learning content, enrolments, progress, assessment activity, and session/token state. It is well suited to local development and small pilots with a single application process.

Administrators can choose a direct HTTPS video URL or upload local MP4, WebM, MOV, M4V, and OGV media. Locally uploaded files live beneath the ignored `data/uploads/` directory. The server stores generated filenames and media metadata in SQLite, but never exposes filesystem paths. It verifies course access before emitting byte ranges for playback.

An upload can use an administrator-provided UTF-8 `.txt`, `.srt`, or `.vtt` transcript, or start a Gemini background transcription job. The service tracks `queued`, `processing`, `ready`, and `failed` states. A generated transcript is explicitly a review-required draft, rather than an authoritative verbatim record.

For broader adoption, use a managed relational database and a migration plan before deploying multiple application instances. Store video with a purpose-built streaming service or object storage/CDN, use expiring access where required, and keep only media metadata and URLs in application data. This avoids database bloat and lets learning videos be delivered efficiently in low-bandwidth settings.

## AI support boundary

The tutor is designed to support learning, not replace programme staff or make high-stakes decisions. A learner's Gemini key is an explicit session-only choice, and all Gemini calls occur through the backend. When a ready transcript is selected, Gemini receives only the bounded lesson transcript and learner question. The prompt requires it to answer only from that material, acknowledge unsupported questions, and avoid professional medical, legal, or safety advice. Without a session key, the product remains usable through its local guided fallback.

Automatic transcription uses Gemini's temporary Files API processing flow only after the administrator selects it. The application deletes the remote processing file after the job whenever possible; the local course upload remains the delivery source. See [the Gemini and video workflow](GEMINI_VIDEO_WORKFLOW.md) for operator-facing details.

Before enabling AI for real learners, define the allowed assistance scope, disclose use of the provider where necessary, minimise personal data sent in prompts, add moderation/escalation paths, and evaluate answers against the NGO's curriculum and safeguarding policies.

## Deployment stages

### Pilot

Run one server process behind a TLS-terminating reverse proxy. Bind the Python process to a private interface, configure environment variables through the hosting platform, place SQLite on durable storage, and remove or reset the development demo accounts.

### Production readiness

Add centralised logs, uptime/health monitoring, error reporting, backup alerts, audit records for administrative changes, rate limits, and periodic dependency/runtime patching. Restrict who can deploy, access data, and manage secrets.

### Scale-out

Move persistent data to a managed relational service before running multiple API instances. Use object storage and a CDN/video host for content delivery, introduce background processing for long-running work, and establish an analytics pipeline with privacy and retention controls.

## Verification

The repository intentionally uses Python's standard library for baseline verification:

```text
python -m compileall -q server.py tests
python -m unittest discover -s tests -v
```

GitHub Actions runs these checks on supported Python versions without installing third-party Python packages.
