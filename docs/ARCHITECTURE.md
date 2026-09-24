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
  |-- course, lesson, enrolment, progress, quiz, and tutor APIs
  |-- SQLite data access
        |                    \
        v                     v
SQLite database           Optional external AI provider
data/learnwithai.db       (server-side key only)

Video/object storage/CDN is referenced by lesson content in production;
large media files should not live in the application repository or SQLite.
```

## Application boundary

`server.py` is the application entry point. It serves `web/` and exposes the backend capabilities through the same origin:

- Account registration, login, logout, and current-user lookups using bearer authentication.
- Learner access to courses and lessons, enrolment, lesson progress, quizzes, and the tutor experience.
- Administrator-only course and lesson management.
- A local tutor fallback when no external AI key is configured.

The server auto-creates its `data/` directory and SQLite schema for local use. Production should place the database on a persistent, access-controlled volume and use a backup strategy that has been tested by restoring it.

## Identity and authorisation

There are two initial roles:

| Role | Primary capability |
| --- | --- |
| `learner` | Discover learning content, enrol, track progress, complete quizzes, and ask for learning support. |
| `admin` | Manage course and lesson content in addition to learner capabilities. |

Passwords are protected with PBKDF2-HMAC. The browser sends bearer tokens to authenticated API endpoints; the server stores hashes of those tokens and enforces expiry. APIs validate incoming data and use parameterized SQLite queries, which keeps untrusted request values separate from SQL instructions.

The static app and API share an origin. This avoids granting a broad cross-origin browser API surface and keeps authentication flows simpler. A production deployment should still use TLS, secure cookie/session choices if cookies are introduced, appropriate rate limiting, and monitoring for brute-force or abusive behaviour.

## Data and media

SQLite is the default source of truth for accounts, learning content, enrolments, progress, assessment activity, and session/token state. It is well suited to local development and small pilots with a single application process.

For broader adoption, use a managed relational database and a migration plan before deploying multiple application instances. Store video with a purpose-built streaming service or object storage/CDN, use expiring access where required, and keep only media metadata and URLs in application data. This avoids database bloat and lets learning videos be delivered efficiently in low-bandwidth settings.

## AI support boundary

The tutor is designed to support learning, not replace programme staff or make high-stakes decisions. With no `OPENAI_API_KEY`, the product remains usable through its local fallback. If an external provider is enabled, the key stays exclusively in the server environment; it must never be sent to the browser or committed to the repository.

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
