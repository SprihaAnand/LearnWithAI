# LearnWithAI

LearnWithAI is a focused learning platform for NGOs and community programmes. It brings together real video lessons, learner progress, short assessments, transcript-grounded Gemini support, and a welcoming web experience.

The initial product is deliberately easy to operate: a dependency-free Python server serves the web application and stores local development data in SQLite. That makes it practical for demonstrations, partner pilots, and a clear path toward a production deployment.

## What it provides

- Learner and administrator accounts.
- Course and lesson publishing with direct hosted-video URLs or private MP4, WebM, MOV, M4V, and OGV uploads.
- Enrolment, completion tracking, and quiz submissions.
- Automatic Gemini draft transcripts or administrator-uploaded `.txt`, `.srt`, and `.vtt` transcripts.
- A session-only Gemini key flow: keys stay in server memory for the authenticated session and are never stored in SQLite, browser storage, URLs, or Git.
- A transcript-grounded tutor that answers from the selected lesson, plus a useful non-AI fallback when Gemini is not configured.
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
| `LEARNWITHAI_MAX_UPLOAD_BYTES` | Maximum complete multipart video request size (1–100 MiB; defaults to 100 MiB). The single-process server admits one upload at a time. |
| `LEARNWITHAI_COOKIE_SECURE` | Set to `1` behind HTTPS to mark private-video playback cookies as `Secure`. Keep `0` only for local HTTP development. |

Keep actual environment files and production secrets out of source control. The local SQLite database contains account and learning data and must also be treated as private.

## Gemini, transcripts, and video

Each signed-in person can add their own Gemini API key from **Gemini session settings**. The browser sends it only to the same-origin backend; it is held in memory for that authenticated session, then cleared on removal, sign-out, expiry, or restart. The app does not persist or expose the key. Use a restricted key and review its quota and billing in Google AI Studio; Google's [API-key security guidance](https://ai.google.dev/gemini-api/docs/api-key) explains why server-side use is important.

When an administrator uploads a lesson video, they can either start automatic Gemini transcription or attach their own UTF-8 `.txt`, `.srt`, or `.vtt` file. Automatic transcription is a background job: the upload is sent to Gemini only after that choice is made, and the resulting transcript is marked as a generated draft that must be reviewed. Learners can watch an uploaded video through a short-lived, HTTP-only, lesson-scoped playback ticket; no reusable bearer token appears in the media URL.

The Gemini tutor receives the selected, ready transcript plus the learner's question and is instructed to answer only from that material. If the transcript does not support an answer, it says so. See [the Gemini and video workflow](docs/GEMINI_VIDEO_WORKFLOW.md) for the detailed data flow and operating guidance.

## Quality checks

Run the same dependency-free checks used by continuous integration:

```powershell
python -m compileall -q server.py tests
python -m unittest discover -s tests -v
```

## Security posture

The application applies PBKDF2-HMAC password hashing, stores only hashes of bearer tokens, expires tokens, validates request input, uses parameterized SQLite queries, and sends security-oriented response headers. Browser access is constrained to the same origin by design. Uploaded videos use protected byte-range streaming and short-lived HTTP-only playback tickets; Gemini keys are memory-only and scoped to the authenticated app session.

Those protections are a strong baseline, not a substitute for deployment hardening. Before handling real learner data, use HTTPS behind a maintained reverse proxy, remove predictable demo users, set a suitable token lifetime, restrict administrative access, keep operating-system and Python patching current, protect backups, and review logs for sensitive data. Never put API keys or learner information in Git commits, screenshots, or client-side code.

## Production and deployment roadmap

1. **Pilot safely.** Deploy the single process behind HTTPS, configure secrets through the host, use a private database volume, and establish encrypted backups plus restore tests.
2. **Operate reliably.** Add health checks, structured logs, error monitoring, rate limits, audit events for admin actions, and a documented incident/backup process.
3. **Scale learning delivery.** Move uploaded video assets to private object storage and a CDN or video-streaming provider with signed playback URLs; keep large media out of Git and SQLite. Move to a managed relational database when concurrent use, reporting, or high availability requires it.
4. **Strengthen the product.** Add organisation-level tenancy, fine-grained roles, accessibility and multilingual review, content moderation, consent/retention controls, and measured learning outcomes.

See [the architecture notes](docs/ARCHITECTURE.md) for the system boundaries and deployment considerations.

## Repository layout

```text
server.py                 Dependency-free HTTP/API server and SQLite integration
web/                      Browser application served by the backend
tests/                    Standard-library unit tests
docs/ARCHITECTURE.md      System design and production guidance
docs/GEMINI_VIDEO_WORKFLOW.md  Gemini, transcript, and media workflow
.github/workflows/ci.yml Dependency-free compile and test checks
```

## License

This project is available under the [MIT License](LICENSE).
