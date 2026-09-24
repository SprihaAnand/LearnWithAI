"""End-to-end checks for the dependency-free LearnWithAI server."""

from __future__ import annotations

import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
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
    ):
        request_headers = {"Connection": "close"}
        if headers:
            request_headers.update(headers)
        body = None
        if payload is not None:
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
        decoded = json.loads(raw.decode("utf-8")) if raw and content_type.startswith("application/json") else raw.decode("utf-8")
        return status, response_headers, decoded

    def login(self, email: str = "learner@learnwithai.demo", password: str = "DemoPass123!") -> str:
        status, _, data = self.request("POST", "/api/auth/login", {"email": email, "password": password})
        self.assertEqual(status, 200, data)
        return data["token"]

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

        status, headers, text = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("LearnWithAI test app", text)
        self.assertEqual(headers["X-Frame-Options"], "DENY")

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

        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            status, _, data = self.request("POST", "/api/tutor", {"question": "How should I practise this skill?"}, token=learner_token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["mode"], "guided_fallback")
        self.assertIn("practise", data["reply"].lower())

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
