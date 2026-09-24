# Video, local transcription, and Gemini workflow

The filename is retained so existing links continue to work. LearnWithAI keeps
video delivery, automatic transcription, and Gemini Q&A as separate boundaries:
an uploaded lesson can be transcribed locally without granting the application
or an administrator a Gemini key.

## Two distinct AI responsibilities

| Capability | Technology and data boundary | Credential requirement |
| --- | --- | --- |
| Automatic lesson transcription | Local faster-whisper job over the uploaded lesson video | No Gemini key or external AI credential. |
| Learner questions about a lesson | Gemini receives the selected ready transcript and the learner's question | The signed-in learner supplies their own session-only Gemini key. |

The automatic path never sends the uploaded video to Gemini. The Q&A path never
uses Gemini to create the transcript.

## Session-only Gemini access for Q&A

1. A signed-in person opens **Gemini session settings** and enters their own
   Gemini API key.
2. The browser sends the key only to the same-origin LearnWithAI backend over
   the active authenticated session.
3. The backend holds the key in memory, keyed to that bearer-token session. It
   is not written to SQLite, upload metadata, browser storage, URLs, logs, or
   source control. Signing out, invalidating the token, removing the key, or
   restarting the server clears it.
4. The backend makes the Gemini Q&A request. A browser never calls Gemini
   directly, and the key is never needed for transcription.

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

## Automatic local transcription with faster-whisper

After selecting an uploaded video, an administrator can choose **Transcribe
automatically**. LearnWithAI records a queued job, then runs faster-whisper
locally over the uploaded file. When successful, the lesson stores the text
with `transcript_source` set to `whisper` and surfaces a generated-transcript
review notice.

This is intentionally asynchronous. The lesson reports `queued`, `processing`,
`ready`, or `failed` rather than claiming a transcript is available immediately.
Until the state is `ready`, the Gemini tutor must not treat that lesson as a
grounded source.

### Deployment and model prerequisites

- Install the pinned runtime dependency with `python -m pip install -r requirements.txt`.
- faster-whisper uses PyAV, whose packaged FFmpeg libraries handle media
  decoding; a separate operating-system `ffmpeg` executable is not required
  for this workflow.
- The first use of a selected model may download model weights. Give the
  service account a writable model cache and either allow that initial network
  access or pre-download/pre-warm the model during image or host preparation.
- `LEARNWITHAI_WHISPER_MODEL` defaults to `base`; the server accepts the
  supported tiny/base/small/medium/large and English/distil model variants.
  `LEARNWITHAI_WHISPER_DEVICE` defaults to `cpu` and accepts `auto`, `cpu`, or
  `cuda`. `LEARNWITHAI_WHISPER_COMPUTE_TYPE` defaults to `int8`; CPU must not
  use `float16` or `int8_float16`.
- CPU execution is supported and is a sensible starting point for a small
  pilot. Model size, video duration, and available CPU/RAM determine job time.
  A CUDA-capable GPU is optional; use a faster-whisper/CTranslate2-compatible
  GPU runtime and verify it in the target deployment rather than assuming a
  host GPU will be used automatically.
- Keep transcription work away from latency-sensitive web traffic as demand
  grows: use bounded concurrency for a pilot, then move it to a dedicated
  worker or queue before scaling the web process.

Automatic transcription produces a draft, not an authoritative record. An
administrator must review and correct the generated text before using it as
captions, course material, evidence of what was taught, or a source for
learner-facing Gemini answers.

## Uploading a human-prepared transcript

An administrator can upload a UTF-8 `.txt`, `.srt`, or `.vtt` file instead of
running faster-whisper. Caption timing and markup are normalised into readable
lesson text, and the original transcript upload is not retained. This remains
the preferred route when a reviewed, approved transcript already exists.

Administrators may also choose **Add it later**. Learners can watch the video,
but the transcript is not ready for Gemini-grounded question answering.

## Transcript-grounded Gemini questions

When a learner asks a question from a lesson with a ready transcript,
LearnWithAI sends Gemini a bounded copy of that lesson's transcript plus the
question. The instruction requires Gemini to answer only from the transcript,
state when the source does not support an answer, and avoid turning course
material into professional medical, legal, or safety advice.

A learner must have a session-only Gemini key and select a lesson with a ready
transcript. Administrators should review generated drafts before relying on
them for learner-facing support. This workflow is
intentionally not a web-search assistant: its value is that answers are
anchored to course material.
