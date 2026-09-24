# Gemini, video, and transcript workflow

LearnWithAI keeps the course video, its transcript, and the learner-facing AI
experience as separate concerns. That makes it possible to use Gemini for a
specific learning session without treating an API key as application data.

## Session-only Gemini access

1. A signed-in person opens **Gemini session settings** and enters their own
   Gemini API key.
2. The browser sends the key only to the same-origin LearnWithAI backend over
   the active authenticated session.
3. The backend holds the key in memory, keyed to that bearer-token session. It
   is not written to SQLite, upload metadata, browser storage, URLs, logs, or
   source control. Signing out, invalidating the token, removing the key, or
   restarting the server clears it.
4. The backend makes every Gemini request. A browser never calls Gemini
   directly.

Use a restricted Gemini key and keep an eye on its quota and billing. Google
explains the current key types and restrictions in its
[API-key guidance](https://ai.google.dev/gemini-api/docs/api-key).

## Adding a lesson video

An administrator can publish a first lesson using either:

- a direct HTTPS URL to a browser-playable video file; or
- an uploaded MP4, WebM, MOV, M4V, or OGV file.

Uploaded files are placed under the server's local `data/uploads/` directory,
which is ignored by Git. Learners stream uploaded videos through an
authenticated, byte-range-capable endpoint; the physical filename is never
returned by the API. The default and maximum upload limit is 100 MiB; it can
be lowered with `LEARNWITHAI_MAX_UPLOAD_BYTES`. This single-process server
admits one multipart upload at a time to keep memory bounded.

For a production deployment, put video objects in managed private object
storage and deliver them through short-lived signed CDN URLs. Local storage is
appropriate for a pilot or a single-server deployment only.

## Choosing a transcript

After selecting an uploaded video, an administrator chooses one of these
paths:

- **Transcribe automatically with Gemini.** The server sends a temporary copy
  of the uploaded video to Gemini's Files API, waits for it to become usable,
  and starts a background transcript job. It stores the generated transcript
  in the lesson when complete and removes the temporary Gemini processing file
  when possible. Generated transcripts must be reviewed before being treated
  as an authoritative record.
- **Upload a transcript.** A UTF-8 `.txt`, `.srt`, or `.vtt` file is accepted.
  Caption timing and markup are normalized into readable lesson text; the
  original uploaded transcript file is not retained.
- **Add it later.** Learners can watch the video, but Gemini does not have
  transcript context for that lesson.

Gemini's Files API processes video asynchronously and keeps provider-side
files temporarily, so LearnWithAI tracks `queued`, `processing`, `ready`, and
`failed` transcript states instead of claiming that the transcript is ready
straight after upload. See Google's [Files API documentation](https://ai.google.dev/gemini-api/docs/files)
and [video understanding guide](https://ai.google.dev/gemini-api/docs/video-understanding).

## Transcript-grounded questions

When a learner asks a question from a lesson, LearnWithAI sends Gemini a
bounded copy of that lesson's transcript plus the question. The instruction
requires Gemini to answer only from the transcript, state when the source does
not support an answer, and avoid turning course material into professional
medical, legal, or safety advice. A learner must be enrolled in the course to
stream an uploaded video or use its transcript as tutor context.

This workflow is intentionally not a web-search assistant. Its value is that
answers are anchored to what the course actually taught.
