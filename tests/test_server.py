"""End-to-end checks for the dependency-free LearnWithAI server."""

from __future__ import annotations

import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import server  # noqa: E402


class LearnWithAIServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.temp_path = Path(cls.tempdir.name)
        cls.web_root = cls.temp_path / "web"
        cls.web_root.mkdir()
        (cls.web_root / "index.html").write_text("<!doctype html><main>LearnWithAI test app</main>", encoding="utf-8")
        cls.database_path = cls.temp_path / "learnwithai.db"
        cls.httpd = server.create_server("127.0.0.1", 0, cls.database_path, cls.web_root, token_ttl_seconds=3600)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        cls.tempdir.cleanup()

    @classmethod
    def request(
        cls,
        method: str,
        path: str,
        payload: object | None = None,
        token: str | None = None,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ):
        request_headers = {"Connection": "close"}
        if headers:
            request_headers.update(headers)
        body = None
        if raw_body is not None:
            body = raw_body
        elif payload is not None:
            body = json.dumps(payload)
            request_headers["Content-Type"] = "application/json"
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=5)
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        response_headers = dict(response.getheaders())
        connection.close()
        content_type = response_headers.get("Content-Type", "")
        if raw and content_type.startswith("application/json"):
            decoded = json.loads(raw.decode("utf-8"))
        elif content_type.startswith("text/") or "html" in content_type:
            decoded = raw.decode("utf-8")
        else:
            decoded = raw
        return status, response_headers, decoded

    @staticmethod
    def multipart_body(fields: dict[str, str], files: dict[str, tuple[str, str, bytes]]) -> tuple[str, bytes]:
        boundary = "----LearnWithAITestBoundary"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )
        for name, (filename, content_type, content) in files.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode(),
                    f"Content-Type: {content_type}\r\n\r\n".encode(),
                    content,
                    b"\r\n",
                ]
            )
        chunks.append(f"--{boundary}--\r\n".encode())
        return f"multipart/form-data; boundary={boundary}", b"".join(chunks)

    def login(self, email: str = "learner@learnwithai.demo", password: str = "DemoPass123!") -> str:
        status, _, data = self.request("POST", "/api/auth/login", {"email": email, "password": password})
        self.assertEqual(status, 200, data)
        return data["token"]

    def course_with_lesson(self, token: str) -> tuple[int, dict[str, object]]:
        status, _, data = self.request("GET", "/api/courses", token=token)
        self.assertEqual(status, 200, data)
        for course in data["courses"]:
            status, _, detail = self.request("GET", f"/api/courses/{course['id']}", token=token)
            self.assertEqual(status, 200, detail)
            if detail["course"]["lessons"]:
                return course["id"], detail["course"]["lessons"][0]
        self.fail("Expected one seeded course with a lesson")

    def test_health_static_and_same_origin_headers(self) -> None:
        status, headers, data = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("Content-Security-Policy", headers)

        origin = f"http://127.0.0.1:{self.port}"
        status, headers, _ = self.request("GET", "/api/health", headers={"Origin": origin})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)

        status, headers, _ = self.request("GET", "/api/health", headers={"Origin": "https://untrusted.example"})
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

        status, headers, _ = self.request("OPTIONS", "/api/session/gemini-key", headers={"Origin": origin})
        self.assertEqual(status, 204)
        self.assertIn("PUT", headers["Access-Control-Allow-Methods"])
        self.assertIn("DELETE", headers["Access-Control-Allow-Methods"])
        self.assertIn("Range", headers["Access-Control-Allow-Headers"])

        status, headers, text = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("LearnWithAI test app", text)
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_upload_limit_is_bounded_for_the_single_process_server(self) -> None:
        with mock.patch.dict(
            server.os.environ,
            {"LEARNWITHAI_MAX_UPLOAD_BYTES": str(server.MAX_SAFE_UPLOAD_BYTES + 1)},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "1 MiB and 100 MiB"):
                server.configured_upload_limit()

    def test_registration_login_me_and_logout(self) -> None:
        email = "new.learner@example.org"
        status, _, data = self.request(
            "POST", "/api/auth/register",
            {"name": "New Learner", "email": email, "password": "StrongPass123"},
        )
        self.assertEqual(status, 201, data)
        self.assertEqual(data["user"]["role"], "learner")
        token = data["token"]

        status, _, data = self.request("GET", "/api/me", token=token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["user"]["email"], email)

        status, _, data = self.request("POST", "/api/auth/logout", {}, token=token)
        self.assertEqual(status, 200, data)
        status, _, data = self.request("GET", "/api/auth/me", token=token)
        self.assertEqual(status, 401, data)
        self.assertEqual(data["error"]["code"], "authentication_required")

        status, _, data = self.request("POST", "/api/auth/login", {"email": email, "password": "wrong"})
        self.assertEqual(status, 401, data)

    def test_course_enrollment_progress_and_quiz(self) -> None:
        token = self.login()
        status, _, data = self.request("GET", "/api/courses", token=token)
        self.assertEqual(status, 200, data)
        self.assertGreaterEqual(len(data["courses"]), 2)
        course_id = data["courses"][0]["id"]

        status, _, data = self.request("GET", f"/api/courses/{course_id}", token=token)
        self.assertEqual(status, 200, data)
        lessons = data["course"]["lessons"]
        self.assertTrue(lessons)

        status, _, data = self.request("POST", f"/api/courses/{course_id}/enroll", {}, token=token)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["enrollment"]["course"]["enrolled"])

        lesson_id = lessons[0]["id"]
        status, _, data = self.request(
            "POST", "/api/progress", {"lesson_id": lesson_id, "progress_percent": 100, "watched_seconds": 90}, token=token
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["progress"]["completed"])

        status, _, data = self.request("GET", f"/api/progress?course_id={course_id}", token=token)
        self.assertEqual(status, 200, data)
        self.assertTrue(any(item["lesson_id"] == lesson_id for item in data["progress"]))

        quiz_lesson = next((item for item in lessons if item["quiz_id"]), None)
        self.assertIsNotNone(quiz_lesson)
        quiz_id = quiz_lesson["quiz_id"]
        status, _, data = self.request("GET", f"/api/quizzes/{quiz_id}", token=token)
        self.assertEqual(status, 200, data)
        question = data["quiz"]["questions"][0]
        self.assertNotIn("answer", question)

        quiz_lesson_id = quiz_lesson["id"]
        self.request("POST", "/api/progress", {"lesson_id": quiz_lesson_id, "progress_percent": 20}, token=token)
        answer = question["options"][0]  # The selected first course has the seeded health quiz.
        status, _, data = self.request("POST", f"/api/quizzes/{quiz_id}/submit", {"answers": {question["id"]: answer}}, token=token)
        self.assertEqual(status, 201, data)
        self.assertGreaterEqual(data["attempt"]["score"], 1)

    def test_rbac_and_tutor_fallback(self) -> None:
        learner_token = self.login()
        course_payload = {
            "title": "A useful new course",
            "summary": "A sufficiently detailed summary for validation.",
            "description": "A sufficiently detailed description for validation and learners.",
            "category": "Test",
            "published": True,
        }
        status, _, data = self.request("POST", "/api/courses", course_payload, token=learner_token)
        self.assertEqual(status, 403, data)
        self.assertEqual(data["error"]["code"], "admin_required")

        admin_token = self.login("admin@learnwithai.demo")
        status, _, data = self.request("POST", "/api/admin/courses", course_payload, token=admin_token)
        self.assertEqual(status, 201, data)
        self.assertEqual(data["course"]["title"], course_payload["title"])

        status, _, data = self.request("POST", "/api/tutor", {"question": "How should I practise this skill?"}, token=learner_token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["mode"], "guided_fallback")
        self.assertIn("practise", data["reply"].lower())

    def test_gemini_session_key_and_transcript_grounded_tutor(self) -> None:
        learner_token = self.login()
        status, _, data = self.request("GET", "/api/session/gemini-key", token=learner_token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data, {"configured": False})

        key = "test-gemini-key-123456789"
        status, _, data = self.request("PUT", "/api/session/gemini-key", {"api_key": key}, token=learner_token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data, {"configured": True})
        self.assertNotIn(key, json.dumps(data))

        token_hash = self.httpd.app._token_hash_from_authorization(f"Bearer {learner_token}")
        with self.httpd.app._gemini_key_lock:
            self.httpd.app._gemini_session_keys[token_hash] = (key, server.now() - 1)
        status, _, data = self.request("GET", "/api/session/gemini-key", token=learner_token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data, {"configured": False})
        status, _, data = self.request("PUT", "/api/session/gemini-key", {"api_key": key}, token=learner_token)
        self.assertEqual(status, 200, data)

        course_id, lesson = self.course_with_lesson(learner_token)
        status, _, enrollment = self.request("POST", f"/api/courses/{course_id}/enroll", {}, token=learner_token)
        self.assertEqual(status, 200, enrollment)
        with mock.patch.object(self.httpd.app, "_gemini_interaction_text", return_value="The lesson says to practise one action at a time.") as interaction:
            status, _, data = self.request(
                "POST",
                "/api/tutor",
                {"question": "What should I practise?", "course_id": course_id, "lesson_id": lesson["id"]},
                token=learner_token,
            )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["mode"], "ai")
        self.assertNotIn(key, json.dumps(data))
        prompt = interaction.call_args.args[1][0]["text"]
        self.assertIn("only facts explicitly supported", prompt)
        self.assertIn("LESSON_TRANSCRIPT", prompt)

        admin_token = self.login("admin@learnwithai.demo")
        status, _, empty_lesson = self.request(
            "POST",
            f"/api/courses/{course_id}/lessons",
            {
                "title": "A lesson awaiting transcript",
                "description": "This lesson intentionally has no transcript yet.",
                "duration_seconds": 0,
                "position": 999,
            },
            token=admin_token,
        )
        self.assertEqual(status, 201, empty_lesson)
        with mock.patch.object(self.httpd.app, "_gemini_interaction_text") as interaction:
            status, _, data = self.request(
                "POST", "/api/tutor", {"question": "What does this say?", "lesson_id": empty_lesson["lesson"]["id"]}, token=learner_token
            )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["mode"], "transcript_required")
        interaction.assert_not_called()

        status, _, data = self.request("DELETE", "/api/session/gemini-key", token=learner_token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data, {"configured": False})
        with mock.patch("server.urllib.request.urlopen", side_effect=AssertionError("network must not be called without a key")):
            status, _, data = self.request(
                "POST", "/api/tutor", {"question": "Can you help me practise?", "lesson_id": lesson["id"]}, token=learner_token
            )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["mode"], "guided_fallback")

        # Logout also removes the memory-only key bound to this exact bearer-token hash.
        self.request("PUT", "/api/session/gemini-key", {"api_key": key}, token=learner_token)
        status, _, _ = self.request("POST", "/api/auth/logout", {}, token=learner_token)
        self.assertEqual(status, 200)
        self.assertNotIn(token_hash, self.httpd.app._gemini_session_keys)

    def test_video_upload_stream_playback_ticket_and_range(self) -> None:
        admin_token = self.login("admin@learnwithai.demo")
        course_id, lesson = self.course_with_lesson(admin_token)
        video = b"\x00\x00\x00\x18ftypisomLearnWithAI-video-bytes"
        transcript_vtt = b"WEBVTT\n\n00:00:00.000 --> 00:00:03.000\nWelcome <b>learners</b>.\n"
        content_type, body = self.multipart_body(
            {"transcription_mode": "upload"},
            {
                "video": ("lesson.mp4", "video/mp4", video),
                "transcript_file": ("lesson.vtt", "text/vtt", transcript_vtt),
            },
        )
        self.assertTrue(self.httpd.app.try_acquire_upload_slot())
        try:
            status, _, data = self.request(
                "POST",
                f"/api/lessons/{lesson['id']}/video",
                token=admin_token,
                headers={"Content-Type": content_type},
                raw_body=body,
            )
        finally:
            self.httpd.app.release_upload_slot()
        self.assertEqual(status, 429, data)
        self.assertEqual(data["error"]["code"], "upload_busy")
        status, _, data = self.request(
            "POST",
            f"/api/lessons/{lesson['id']}/video",
            token=admin_token,
            headers={"Content-Type": content_type},
            raw_body=body,
        )
        self.assertEqual(status, 201, data)
        uploaded = data["lesson"]
        self.assertTrue(uploaded["has_uploaded_video"])
        self.assertEqual(uploaded["transcription_status"], "ready")
        self.assertEqual(uploaded["transcript_source"], "uploaded")
        self.assertEqual(uploaded["transcript"], "Welcome learners.")
        self.assertEqual(uploaded["stream_url"], f"/api/lessons/{lesson['id']}/stream")
        self.assertNotIn("uploads", json.dumps(uploaded))

        status, _, registration = self.request(
            "POST",
            "/api/auth/register",
            {"name": "Video Learner", "email": "video.learner@example.org", "password": "VideoPass123"},
        )
        self.assertEqual(status, 201, registration)
        learner_token = registration["token"]
        status, _, data = self.request(
            "POST",
            f"/api/lessons/{lesson['id']}/video",
            token=learner_token,
            headers={"Content-Type": content_type},
            raw_body=body,
        )
        self.assertEqual(status, 403, data)
        self.assertEqual(data["error"]["code"], "admin_required")
        status, _, data = self.request("GET", f"/api/lessons/{lesson['id']}/stream", token=learner_token)
        self.assertEqual(status, 403, data)
        # Published course listings are discoverable, but video-derived transcripts
        # and upload metadata remain within an enrolled learner's access.
        status, _, data = self.request("GET", f"/api/lessons/{lesson['id']}", token=learner_token)
        self.assertEqual(status, 200, data)
        self.assertNotIn("transcript", data["lesson"])
        self.assertFalse(data["lesson"]["transcript_available"])
        self.assertFalse(data["lesson"]["transcript_access"])
        self.assertNotIn("transcription_error", data["lesson"])
        self.assertNotIn("video_size_bytes", data["lesson"])
        status, _, data = self.request("GET", f"/api/courses/{course_id}/lessons", token=learner_token)
        self.assertEqual(status, 200, data)
        self.assertNotIn("transcript", data["lessons"][0])
        status, _, data = self.request("POST", f"/api/courses/{course_id}/enroll", {}, token=learner_token)
        self.assertEqual(status, 200, data)

        status, _, data = self.request("GET", f"/api/lessons/{lesson['id']}", token=learner_token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["lesson"]["transcript"], "Welcome learners.")
        self.assertTrue(data["lesson"]["transcript_access"])

        status, headers, data = self.request(
            "GET", f"/api/lessons/{lesson['id']}/stream", token=learner_token, headers={"Range": "bytes=0-3"}
        )
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], f"bytes 0-3/{len(video)}")
        self.assertEqual(headers["Cache-Control"], "private, no-store")
        self.assertEqual(data, video[:4])

        status, headers, data = self.request("GET", f"/api/lessons/{lesson['id']}/playback-ticket", token=learner_token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data, {"ok": True})
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertIn(f"Path=/api/lessons/{lesson['id']}/stream", headers["Set-Cookie"])
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertNotIn(cookie.split("=", 1)[1], json.dumps(data))

        status, _, data = self.request(
            "GET", f"/api/lessons/{lesson['id']}/stream", headers={"Cookie": cookie, "Range": "bytes=-5"}
        )
        self.assertEqual(status, 206)
        self.assertEqual(data, video[-5:])
        status, headers, data = self.request(
            "GET", f"/api/lessons/{lesson['id']}/stream", headers={"Cookie": cookie, "Range": "bytes=999-"}
        )
        self.assertEqual(status, 416)
        self.assertEqual(headers["Content-Range"], f"bytes */{len(video)}")
        self.assertEqual(data, b"")

    def test_automatic_transcription_is_queued_and_mocked(self) -> None:
        admin_token = self.login("admin@learnwithai.demo")
        _, lesson = self.course_with_lesson(admin_token)
        content_type, body = self.multipart_body(
            {"transcription_mode": "auto"},
            {"video": ("automatic.webm", "video/webm", b"webm-video-content")},
        )
        with mock.patch.object(self.httpd.app, "_start_transcription_job") as start_job:
            status, _, data = self.request(
                "POST",
                f"/api/lessons/{lesson['id']}/video",
                token=admin_token,
                headers={"Content-Type": content_type},
                raw_body=body,
            )
        self.assertEqual(status, 201, data)
        queued = data["lesson"]
        self.assertEqual(queued["transcription_status"], "queued")
        self.assertEqual(queued["transcript_source"], "whisper")
        self.assertIn("review", queued["transcript_notice"].lower())
        start_job.assert_called_once()

        with self.httpd.app.connect() as db:
            row = db.execute("SELECT video_storage_name FROM lessons WHERE id = ?", (lesson["id"],)).fetchone()
            storage_name = row["video_storage_name"]
        with mock.patch.object(self.httpd.app, "_whisper_transcribe_video", return_value="[00:00 - 00:03] Welcome to the lesson."):
            self.httpd.app._run_transcription_job(lesson["id"], storage_name)
        status, _, data = self.request("GET", f"/api/lessons/{lesson['id']}", token=admin_token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["lesson"]["transcription_status"], "ready")
        self.assertEqual(data["lesson"]["transcript_source"], "whisper")
        self.assertEqual(data["lesson"]["transcript"], "[00:00 - 00:03] Welcome to the lesson.")
        self.assertIn("not a verbatim", data["lesson"]["transcript_notice"])

    def test_whisper_engine_is_lazy_cached_and_preserves_segment_timestamps(self) -> None:
        class Segment:
            def __init__(self, start: float, end: float, text: str) -> None:
                self.start = start
                self.end = end
                self.text = text

        fake_model = mock.Mock()
        fake_model.transcribe.return_value = (
            [Segment(0.4, 2.9, " Welcome to the lesson. "), Segment(65.2, 67.8, " Practise one step at a time.")],
            object(),
        )
        factory = mock.Mock(return_value=fake_model)
        fake_module = types.SimpleNamespace(WhisperModel=factory)
        app = self.httpd.app
        with app._whisper_model_lock:
            app._whisper_model = None
            app._whisper_model_config = None
        with (
            mock.patch.dict(
                os.environ,
                {
                    "LEARNWITHAI_WHISPER_MODEL": "base",
                    "LEARNWITHAI_WHISPER_DEVICE": "cpu",
                    "LEARNWITHAI_WHISPER_COMPUTE_TYPE": "int8",
                },
            ),
            mock.patch.object(server.importlib, "import_module", return_value=fake_module) as import_module,
        ):
            first = app._whisper_transcribe_video(Path("lesson.webm"))
            second = app._whisper_transcribe_video(Path("lesson.webm"))
        self.assertEqual(first, "[00:00 - 00:02] Welcome to the lesson.\n[01:05 - 01:07] Practise one step at a time.")
        self.assertEqual(second, first)
        factory.assert_called_once_with("base", device="cpu", compute_type="int8")
        self.assertEqual(import_module.call_count, 1)
        self.assertEqual(fake_model.transcribe.call_count, 2)
        fake_model.transcribe.assert_called_with("lesson.webm", beam_size=5, vad_filter=True)

    def test_whisper_model_load_failure_is_actionable_without_leaking_runtime_details(self) -> None:
        app = self.httpd.app
        with app._whisper_model_lock:
            app._whisper_model = None
            app._whisper_model_config = None
        factory = mock.Mock(side_effect=RuntimeError(r"C:\private-model-cache\missing.bin"))
        fake_module = types.SimpleNamespace(WhisperModel=factory)
        with (
            mock.patch.dict(
                os.environ,
                {
                    "LEARNWITHAI_WHISPER_MODEL": "base",
                    "LEARNWITHAI_WHISPER_DEVICE": "cpu",
                    "LEARNWITHAI_WHISPER_COMPUTE_TYPE": "int8",
                },
            ),
            mock.patch.object(server.importlib, "import_module", return_value=fake_module),
            self.assertRaises(server.WhisperTranscriptionError) as raised,
        ):
            app._get_whisper_model()
        self.assertIn("could not be loaded", raised.exception.public_message)
        self.assertNotIn("private-model-cache", raised.exception.public_message)

    def test_auto_transcription_surfaces_missing_whisper_runtime_safely(self) -> None:
        admin_token = self.login("admin@learnwithai.demo")
        _, lesson = self.course_with_lesson(admin_token)
        content_type, body = self.multipart_body(
            {"transcription_mode": "auto"},
            {"video": ("needs-whisper.mp4", "video/mp4", b"not-a-real-video")},
        )
        with mock.patch.object(self.httpd.app, "_start_transcription_job"):
            status, _, data = self.request(
                "POST",
                f"/api/lessons/{lesson['id']}/video",
                token=admin_token,
                headers={"Content-Type": content_type},
                raw_body=body,
            )
        self.assertEqual(status, 201, data)
        with self.httpd.app.connect() as db:
            row = db.execute("SELECT video_storage_name FROM lessons WHERE id = ?", (lesson["id"],)).fetchone()
            storage_name = row["video_storage_name"]
        with self.httpd.app._whisper_model_lock:
            self.httpd.app._whisper_model = None
            self.httpd.app._whisper_model_config = None
        with mock.patch.object(server.importlib, "import_module", side_effect=ModuleNotFoundError("faster_whisper")):
            self.httpd.app._run_transcription_job(lesson["id"], storage_name)
        status, _, data = self.request("GET", f"/api/lessons/{lesson['id']}", token=admin_token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["lesson"]["transcription_status"], "failed")
        self.assertEqual(data["lesson"]["transcript_source"], "whisper")
        self.assertIn("faster-whisper", data["lesson"]["transcription_error"])
        self.assertIn("pip install", data["lesson"]["transcription_error"])

    def test_hosted_video_transcript_supports_the_normalized_limit(self) -> None:
        admin_token = self.login("admin@learnwithai.demo")
        course_id, _ = self.course_with_lesson(admin_token)
        transcript = "A" * 200_000
        status, _, data = self.request(
            "POST",
            f"/api/courses/{course_id}/lessons",
            {
                "title": "Hosted lesson with a full transcript",
                "description": "A hosted lesson that verifies the supported transcript size.",
                "video_url": "https://media.example.org/lesson.mp4",
                "duration_seconds": 120,
                "position": 9_999,
                "transcript": transcript,
            },
            token=admin_token,
        )
        self.assertEqual(status, 201, data)
        self.assertEqual(len(data["lesson"]["transcript"]), len(transcript))

    def test_seed_happens_once(self) -> None:
        # A separate database makes the first-run marker easy to verify without mutating the live test server.
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "seed.db"
            server.LearnWithAIApp(db_path, self.web_root)
            db = sqlite3.connect(db_path)
            try:
                first_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            finally:
                db.close()
            server.LearnWithAIApp(db_path, self.web_root)
            db = sqlite3.connect(db_path)
            try:
                second_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            finally:
                db.close()
            self.assertEqual(first_count, 2)
            self.assertEqual(second_count, 2)

    def test_existing_lesson_table_gets_upload_metadata_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.db"
            db = sqlite3.connect(db_path)
            try:
                db.execute(
                    """CREATE TABLE lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    video_url TEXT,
                    duration_seconds INTEGER NOT NULL DEFAULT 0,
                    position INTEGER NOT NULL,
                    transcript TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(course_id, position)
                    )"""
                )
                db.commit()
            finally:
                db.close()
            server.LearnWithAIApp(db_path, self.web_root)
            db = sqlite3.connect(db_path)
            try:
                columns = {row[1] for row in db.execute("PRAGMA table_info(lessons)").fetchall()}
            finally:
                db.close()
            self.assertTrue(
                {
                    "video_storage_name",
                    "video_mime_type",
                    "video_size_bytes",
                    "transcription_status",
                    "transcript_source",
                    "transcription_error",
                }.issubset(columns)
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
