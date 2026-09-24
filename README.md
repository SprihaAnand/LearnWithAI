# LearnWithAI

LearnWithAI is a focused learning platform for NGOs and community programmes. It brings together structured video lessons, learner progress, short assessments, and an optional AI learning companion in one simple web experience.

The initial product is deliberately easy to operate: a dependency-free Python server serves the web application and stores local development data in SQLite. That makes it practical for demonstrations, partner pilots, and a clear path toward a production deployment.

## What it provides

- Learner and administrator accounts.
- Course and lesson publishing, including video lesson metadata.
- Enrolment, completion tracking, and quiz submissions.
- A lesson-aware tutor endpoint with an offline fallback; an external AI key is optional.
- A same-origin web application, served by the backend, so browser/API deployment is straightforward.

## Run locally

Prerequisite: Python 3.11 or later. No package installation is required.

```powershell
cd C:\Users\Spriha\Desktop\LearnWithAI
python server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). On first startup, the server creates `data/learnwithai.db` and its schema automatically.

The server reads configuration from process environment variables. `.env.example` is a safe reference file; it is not automatically loaded by Python. For a one-off local override in PowerShell:

```powershell
$env:LEARNWITHAI_PORT = "8001"
python server.py
```

## Development demo accounts

For a newly created local database only, the app seeds predictable demo accounts:

| Role | Email | Password |
| --- | --- | --- |
| Administrator | `admin@learnwithai.demo` | `DemoPass123!` |
| Learner | `learner@learnwithai.demo` | `DemoPass123!` |

These credentials exist strictly to support local development and demonstrations. Do not deploy them, reuse the password, or make them available in a public environment.

## Configuration

Copy the variable names from [`.env.example`](.env.example) into your deployment environment. The supported values are:

| Variable | Purpose |
| --- | --- |
| `LEARNWITHAI_DB` | SQLite database file path. Defaults to `data/learnwithai.db`. |
| `LEARNWITHAI_WEB_ROOT` | Directory containing the static web application. Defaults to `web`. |
| `LEARNWITHAI_HOST` | Bind address. Defaults to `127.0.0.1`. |
| `LEARNWITHAI_PORT` | HTTP port. Defaults to `8000`. |
| `LEARNWITHAI_TOKEN_TTL_SECONDS` | Lifetime for authenticated sessions/tokens. |
| `OPENAI_API_KEY` | Optional external AI-provider key. Without it, tutoring uses the built-in fallback. |

Keep actual environment files and production secrets out of source control. The local SQLite database contains account and learning data and must also be treated as private.

## Quality checks

Run the same dependency-free checks used by continuous integration:

```powershell
python -m compileall -q server.py tests
python -m unittest discover -s tests -v
```

## Security posture

The application applies PBKDF2-HMAC password hashing, stores only hashes of bearer tokens, expires tokens, validates request input, uses parameterized SQLite queries, and sends security-oriented response headers. Browser access is constrained to the same origin by design.

Those protections are a strong baseline, not a substitute for deployment hardening. Before handling real learner data, use HTTPS behind a maintained reverse proxy, remove predictable demo users, set a suitable token lifetime, restrict administrative access, keep operating-system and Python patching current, protect backups, and review logs for sensitive data. Never put API keys or learner information in Git commits, screenshots, or client-side code.

## Production and deployment roadmap

1. **Pilot safely.** Deploy the single process behind HTTPS, configure secrets through the host, use a private database volume, and establish encrypted backups plus restore tests.
2. **Operate reliably.** Add health checks, structured logs, error monitoring, rate limits, audit events for admin actions, and a documented incident/backup process.
3. **Scale learning delivery.** Put video assets behind object storage and a CDN or video-streaming provider; keep large media out of Git and SQLite. Move to a managed relational database when concurrent use, reporting, or high availability requires it.
4. **Strengthen the product.** Add organisation-level tenancy, fine-grained roles, accessibility and multilingual review, content moderation, consent/retention controls, and measured learning outcomes.

See [the architecture notes](docs/ARCHITECTURE.md) for the system boundaries and deployment considerations.

## Repository layout

```text
server.py                 Dependency-free HTTP/API server and SQLite integration
web/                      Browser application served by the backend
tests/                    Standard-library unit tests
docs/ARCHITECTURE.md      System design and production guidance
.github/workflows/ci.yml Dependency-free compile and test checks
```

## License

This project is available under the [MIT License](LICENSE).
