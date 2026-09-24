# LearnWithAI

LearnWithAI is a focused learning platform for NGOs and community programmes. It brings together real video lessons, learner progress, short assessments, transcript-grounded Gemini support, and a welcoming web experience.

The initial product is deliberately easy to operate: a small Python server serves the web application and stores local development data in SQLite. That makes it practical for demonstrations, partner pilots, and a clear path toward a production deployment.

## What it provides

- Learner and administrator accounts.
- Course and lesson publishing with direct hosted-video URLs or private MP4, WebM, MOV, M4V, and OGV uploads.
- Enrolment, completion tracking, and quiz submissions.
- Automatic local faster-whisper draft transcripts or administrator-uploaded `.txt`, `.srt`, and `.vtt` transcripts.
- A session-only Gemini key flow: keys stay in server memory for the authenticated session and are never stored in SQLite, browser storage, URLs, or Git.
- A session-keyed Gemini tutor that answers only from the selected, ready lesson transcript.
- A same-origin web application, served by the backend, so browser/API deployment is straightforward.

## Run locally

Prerequisite: Python 3.11 or later. Install the service dependencies before using automatic transcription:

```powershell
cd path\to\LearnWithAI
python -m pip install -r requirements.txt
python server.py
```

Automatic transcription uses `faster-whisper`. Its PyAV dependency bundles the
FFmpeg libraries it needs, so a separate system `ffmpeg` executable is not a
runtime requirement. The first use of a model may download model weights; make
the model cache writable and either allow that initial download or pre-warm the
cache during deployment. CPU execution is supported. A CUDA-capable GPU is an
optional deployment optimisation and must use libraries compatible with the
selected faster-whisper/CTranslate2 build.

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
| `LEARNWITHAI_MAX_UPLOAD_BYTES` | Maximum video file size (1–100 MiB; defaults to 100 MiB). The multipart request also allows a transcript of up to 5 MiB and bounded form overhead. The single-process server admits one upload at a time. |
| `LEARNWITHAI_COOKIE_SECURE` | Set to `1` behind HTTPS to mark private-video playback cookies as `Secure`. Keep `0` only for local HTTP development. |
| `LEARNWITHAI_WHISPER_MODEL` | Local faster-whisper model name. Defaults to `base`; choose a supported model appropriate for your language coverage, latency, and RAM/GPU budget. |
| `LEARNWITHAI_WHISPER_DEVICE` | Local transcription device: `cpu` (default), `auto`, or `cuda`. |
| `LEARNWITHAI_WHISPER_COMPUTE_TYPE` | faster-whisper compute type. Defaults to `int8`; CPU must not use `float16` or `int8_float16`. |

Keep actual environment files and production secrets out of source control. The local SQLite database contains account and learning data and must also be treated as private.

Automatic transcription is local. These Whisper settings need no API key. The
first selected model may need to download weights into the service account's
cache, so pre-warm that model in a production image or permit the initial
download during deployment.

## Local transcription, Gemini Q&A, and video

An administrator who uploads a lesson video can choose automatic local
transcription or attach their own UTF-8 `.txt`, `.srt`, or `.vtt` file.
Automatic jobs run locally with faster-whisper in the background: the video is
not sent to Gemini and no Gemini API key is needed. The resulting transcript is
stored as a `whisper`-sourced, review-required draft. An administrator must
check and correct it before relying on it as curriculum, captions, or learner
support context. Learners can watch an uploaded video through a short-lived,
HTTP-only, lesson-scoped playback ticket; no reusable bearer token appears in
the media URL.

Gemini is used only for Q&A. Each signed-in person can add their own Gemini API
key from **Gemini session settings**. The browser sends it only to the
same-origin backend; it is held in memory for that authenticated session, then
cleared on removal, sign-out, expiry, or restart. The app does not persist or
expose the key. Use a restricted key and review its quota and billing in Google
AI Studio; Google's [API-key security guidance](https://ai.google.dev/gemini-api/docs/api-key)
explains why server-side use is important.

The Gemini tutor receives the selected, ready transcript plus the learner's
question and is instructed to answer only from that material. If the transcript
does not support an answer, it says so. A Gemini key never controls automatic
transcription. See [the Gemini and video workflow](docs/GEMINI_VIDEO_WORKFLOW.md)
for the detailed data flow and operating guidance.

Saving a Gemini key confirms it was added to your session; Gemini checks access
when you ask a question. The assistant distinguishes invalid keys, permission
problems, exhausted quota, network failures, and responses without usable text,
with guidance for each. If a request times out, try again; the default timeout
is 60 seconds. After a server restart, add your key again through the app's
session settings. Never paste it into source files, logs, or support messages.

The course studio displays the server's video and transcript size limits and
checks files before creating a course. If a video exceeds the limit, compress
or split it, or use a hosted direct video URL with your own transcript.
If an upload is interrupted, keep the selected file and check that the app is
open at the Python server's address (by default `http://127.0.0.1:8000`), then
retry. The server must stay running while you use the app.

## Quality checks

Run the same checks used by continuous integration:

```powershell
python -m pip install -r requirements.txt
python -m compileall -q server.py tests
python -m unittest discover -s tests -v
node --test tests/test_upload_ui.cjs
```

## Security posture

The application applies PBKDF2-HMAC password hashing, stores only hashes of bearer tokens, expires tokens, validates request input, uses parameterized SQLite queries, and sends security-oriented response headers. Browser access is constrained to the same origin by design. Uploaded videos use protected byte-range streaming and short-lived HTTP-only playback tickets; Gemini keys are memory-only and scoped to the authenticated app session. Automatic transcripts are generated locally by faster-whisper, marked for review, and are not sent to Gemini.

Those protections are a strong baseline, not a substitute for deployment hardening. Before handling real learner data, use HTTPS behind a maintained reverse proxy, remove predictable demo users, set a suitable token lifetime, restrict administrative access, keep operating-system and Python patching current, protect backups, and review logs for sensitive data. Never put API keys or learner information in Git commits, screenshots, or client-side code.

## Production and deployment roadmap

1. **Pilot safely.** Deploy the single process behind HTTPS, configure secrets through the host, use a private database volume, and establish encrypted backups plus restore tests.
2. **Operate reliably.** Add health checks, structured logs, error monitoring, rate limits, audit events for admin actions, and a documented incident/backup process. Pre-warm and monitor the faster-whisper model cache, and reserve enough CPU/RAM (or a compatible GPU) for background transcription.
3. **Scale learning delivery.** Move uploaded video assets to private object storage and a CDN or video-streaming provider with signed playback URLs; keep large media out of Git and SQLite. Move transcription into an isolated worker/queue when it can contend with learner traffic, and move to a managed relational database when concurrent use, reporting, or high availability requires it.
4. **Strengthen the product.** Add organisation-level tenancy, fine-grained roles, accessibility and multilingual review, content moderation, consent/retention controls, and measured learning outcomes.

See [the architecture notes](docs/ARCHITECTURE.md) for the system boundaries and deployment considerations.

## Repository layout

```text
server.py                 HTTP/API server and SQLite integration
web/                      Browser application served by the backend
tests/                    Standard-library unit tests
docs/ARCHITECTURE.md      System design and production guidance
docs/GEMINI_VIDEO_WORKFLOW.md  Local transcription, Gemini Q&A, and media workflow
requirements.txt          Runtime dependency pin for local faster-whisper transcription
.github/workflows/ci.yml Runtime install, compile, and test checks
```

## License

This project is available under the [MIT License](LICENSE).
