#!/usr/bin/env python3
"""LearnWithAI's dependency-free development server.

It intentionally uses only the Python standard library so a new NGO deployment
can be evaluated without a separate application server or a package install.
Run ``python server.py`` and open http://127.0.0.1:8000.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional


APP_NAME = "LearnWithAI"
APP_VERSION = "1.0.0"
PBKDF2_ITERATIONS = 310_000
MAX_BODY_BYTES = 1_000_000
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class APIError(Exception):
    """An expected, safe-to-return API error."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


class ManagedConnection(sqlite3.Connection):
    """SQLite's context manager commits but does not close on its own.

    Closing here matters on Windows, where lingering connections keep the database
    file locked after a request or a short-lived test has finished.
    """

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def now() -> int:
    return int(time.time())


def iso_timestamp(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def as_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise APIError(400, "invalid_input", f"{field} must be true or false.")


def clean_text(value: Any, field: str, minimum: int = 0, maximum: int = 5000, *, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise APIError(400, "invalid_input", f"{field} must be text.")
    result = value.strip()
    if len(result) < minimum:
        raise APIError(400, "invalid_input", f"{field} must be at least {minimum} characters.")
    if len(result) > maximum:
        raise APIError(400, "invalid_input", f"{field} must be at most {maximum} characters.")
    if any(ord(char) < 32 and char not in "\n\t" for char in result):
        raise APIError(400, "invalid_input", f"{field} contains invalid control characters.")
    return result


def clean_url(value: Any, field: str, *, required: bool = False) -> Optional[str]:
    if value in (None, ""):
        if required:
            raise APIError(400, "invalid_input", f"{field} is required.")
        return None
    result = clean_text(value, field, 1, 2048)
    parsed = urllib.parse.urlparse(result)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise APIError(400, "invalid_input", f"{field} must be an http or https URL.")
    return result


def clean_identifier(value: str, field: str = "id") -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise APIError(404, "not_found", "The requested resource was not found.") from None
    if parsed < 1:
        raise APIError(404, "not_found", "The requested resource was not found.")
    return parsed


def public_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "role": row["role"],
        "created_at": iso_timestamp(row["created_at"]),
    }


class LearnWithAIApp:
    """Application service and SQLite persistence layer."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        web_root: str | Path | None = None,
        token_ttl_seconds: Optional[int] = None,
    ) -> None:
        root = Path(__file__).resolve().parent
        self.database_path = Path(database_path or os.environ.get("LEARNWITHAI_DB", root / "data" / "learnwithai.db"))
        self.web_root = Path(web_root or os.environ.get("LEARNWITHAI_WEB_ROOT", root / "web"))
        self.token_ttl_seconds = token_ttl_seconds or int(os.environ.get("LEARNWITHAI_TOKEN_TTL_SECONDS", "604800"))
        if self.token_ttl_seconds < 300:
            raise ValueError("LEARNWITHAI_TOKEN_TTL_SECONDS must be at least 300 seconds")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15, factory=ManagedConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_database(self) -> None:
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('learner', 'admin')) DEFAULT 'learner',
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_tokens_user_idx ON auth_tokens(user_id);
                CREATE TABLE IF NOT EXISTS courses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    description TEXT NOT NULL,
                    category TEXT NOT NULL,
                    thumbnail_url TEXT,
                    published INTEGER NOT NULL DEFAULT 0 CHECK(published IN (0, 1)),
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    video_url TEXT,
                    duration_seconds INTEGER NOT NULL DEFAULT 0,
                    position INTEGER NOT NULL,
                    transcript TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(course_id, position)
                );
                CREATE INDEX IF NOT EXISTS lessons_course_idx ON lessons(course_id, position);
                CREATE TABLE IF NOT EXISTS enrollments (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                    enrolled_at INTEGER NOT NULL,
                    PRIMARY KEY(user_id, course_id)
                );
                CREATE TABLE IF NOT EXISTS lesson_progress (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
                    progress_percent REAL NOT NULL DEFAULT 0 CHECK(progress_percent >= 0 AND progress_percent <= 100),
                    watched_seconds INTEGER NOT NULL DEFAULT 0 CHECK(watched_seconds >= 0),
                    completed_at INTEGER,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(user_id, lesson_id)
                );
                CREATE TABLE IF NOT EXISTS quizzes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lesson_id INTEGER NOT NULL UNIQUE REFERENCES lessons(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    questions_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS quiz_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
                    answers_json TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    max_score INTEGER NOT NULL,
                    submitted_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS quiz_attempts_user_quiz_idx ON quiz_attempts(user_id, quiz_id);
                """
            )
            seed_state = db.execute("SELECT value FROM settings WHERE key = 'seeded_at'").fetchone()
            if seed_state is None:
                self._seed_demo_data(db)
                db.execute("INSERT INTO settings(key, value) VALUES('seeded_at', ?)", (str(now()),))

    @staticmethod
    def _password_parts(password: str) -> tuple[str, str]:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
        return salt.hex(), digest.hex()

    @staticmethod
    def _verify_password(password: str, salt_hex: str, expected_hex: str) -> bool:
        try:
            salt = bytes.fromhex(salt_hex)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS).hex()
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, expected_hex)

    def _seed_demo_data(self, db: sqlite3.Connection) -> None:
        """Populate a small, useful demo once. A settings marker prevents reseeding."""
        timestamp = now()
        admin_salt, admin_hash = self._password_parts("DemoPass123!")
        learner_salt, learner_hash = self._password_parts("DemoPass123!")
        db.execute(
            "INSERT INTO users(name, email, password_salt, password_hash, role, created_at) VALUES (?, ?, ?, ?, 'admin', ?)",
            ("LearnWithAI Admin", "admin@learnwithai.demo", admin_salt, admin_hash, timestamp),
        )
        admin_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
        db.execute(
            "INSERT INTO users(name, email, password_salt, password_hash, role, created_at) VALUES (?, ?, ?, ?, 'learner', ?)",
            ("Demo Learner", "learner@learnwithai.demo", learner_salt, learner_hash, timestamp),
        )

        courses = [
            (
                "Digital Skills Foundations",
                "Build confident, practical habits for using digital tools safely.",
                "A friendly starting point for learners building confidence with devices, passwords, online forms, and communication.",
                "Digital skills",
            ),
            (
                "Community Health Essentials",
                "Learn how to find reliable health information and support your community.",
                "Short, discussion-friendly lessons for community volunteers and learners who want to evaluate health information thoughtfully.",
                "Community wellbeing",
            ),
        ]
        course_ids: list[int] = []
        for title, summary, description, category in courses:
            db.execute(
                """INSERT INTO courses(title, summary, description, category, thumbnail_url, published, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, NULL, 1, ?, ?, ?)""",
                (title, summary, description, category, admin_id, timestamp, timestamp),
            )
            course_ids.append(int(db.execute("SELECT last_insert_rowid()").fetchone()[0]))

        lesson_rows = [
            (course_ids[0], "Getting started with a device", "Identify the basics of a smartphone or computer and choose one small skill to practise.", None, 420, 1, "Start with the device you have. Practise one action at a time, then explain it in your own words."),
            (course_ids[0], "Passwords and account safety", "Create stronger passwords and recognise common account-security risks.", None, 510, 2, "Use a unique passphrase for each important account. Never share a one-time verification code."),
            (course_ids[1], "Finding reliable information", "Use simple checks before sharing health information with family or neighbours.", None, 480, 1, "Check the source, date, evidence, and whether a qualified local professional can confirm an important claim."),
            (course_ids[1], "Listening with empathy", "Practise a respectful conversation when someone is worried about their wellbeing.", None, 390, 2, "Listen first, avoid diagnosing, and help someone find appropriate local support when needed."),
        ]
        lesson_ids: list[int] = []
        for values in lesson_rows:
            db.execute(
                """INSERT INTO lessons(course_id, title, description, video_url, duration_seconds, position, transcript, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*values, timestamp, timestamp),
            )
            lesson_ids.append(int(db.execute("SELECT last_insert_rowid()").fetchone()[0]))

        questions = [
            {
                "id": "password-1",
                "question": "Which choice is the strongest way to protect an important account?",
                "options": [
                    "Use the same short password everywhere",
                    "Use a unique passphrase and keep verification codes private",
                    "Share your password with a friend",
                    "Write your password in a public post",
                ],
                "answer": "Use a unique passphrase and keep verification codes private",
                "explanation": "Unique passphrases and private verification codes reduce the impact of a stolen password.",
            },
            {
                "id": "password-2",
                "question": "What should you do with a one-time verification code sent to your phone?",
                "options": ["Keep it private", "Post it online", "Share it with anyone who asks", "Use it as your name"],
                "answer": "Keep it private",
                "explanation": "A verification code can allow another person to enter your account.",
            },
        ]
        db.execute(
            "INSERT INTO quizzes(lesson_id, title, questions_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (lesson_ids[1], "Account safety check", json.dumps(questions), timestamp, timestamp),
        )
        health_questions = [
            {
                "id": "source-1",
                "question": "Before sharing health information, what is a useful first check?",
                "options": ["Check the source and date", "Share it immediately", "Ignore who published it", "Assume all posts are correct"],
                "answer": "Check the source and date",
                "explanation": "Checking the source and date helps you identify outdated or unreliable claims.",
            }
        ]
        db.execute(
            "INSERT INTO quizzes(lesson_id, title, questions_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (lesson_ids[2], "Reliable information check", json.dumps(health_questions), timestamp, timestamp),
        )

    def validate_registration(self, data: dict[str, Any]) -> tuple[str, str, str]:
        name = clean_text(data.get("name", data.get("full_name")), "name", 2, 80)
        email = clean_text(data.get("email"), "email", 3, 254).lower()
        if not EMAIL_PATTERN.fullmatch(email):
            raise APIError(400, "invalid_input", "email must be a valid email address.")
        password = clean_text(data.get("password"), "password", 10, 128)
        if not any(character.isalpha() for character in password) or not any(character.isdigit() for character in password):
            raise APIError(400, "weak_password", "password must include a letter and a number.")
        return name, email, password

    def register(self, data: dict[str, Any]) -> tuple[dict[str, Any], str]:
        name, email, password = self.validate_registration(data)
        salt, password_hash = self._password_parts(password)
        timestamp = now()
        try:
            with self.connect() as db:
                cursor = db.execute(
                    "INSERT INTO users(name, email, password_salt, password_hash, role, created_at) VALUES (?, ?, ?, ?, 'learner', ?)",
                    (name, email, salt, password_hash, timestamp),
                )
                user = db.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
                token = self._create_token(db, int(cursor.lastrowid))
        except sqlite3.IntegrityError:
            raise APIError(409, "email_in_use", "An account already exists for that email address.") from None
        return public_user(user), token

    def login(self, data: dict[str, Any]) -> tuple[dict[str, Any], str]:
        email = clean_text(data.get("email"), "email", 3, 254).lower()
        password = clean_text(data.get("password"), "password", 1, 128)
        with self.connect() as db:
            user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user is None or not self._verify_password(password, user["password_salt"], user["password_hash"]):
                raise APIError(401, "invalid_credentials", "Email or password is incorrect.")
            token = self._create_token(db, int(user["id"]))
        return public_user(user), token

    def _create_token(self, db: sqlite3.Connection, user_id: int) -> str:
        timestamp = now()
        db.execute("DELETE FROM auth_tokens WHERE expires_at <= ?", (timestamp,))
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        db.execute(
            "INSERT INTO auth_tokens(token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, timestamp + self.token_ttl_seconds, timestamp),
        )
        return raw_token

    def authenticate(self, authorization: Optional[str]) -> dict[str, Any]:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise APIError(401, "authentication_required", "Provide a valid bearer token.")
        token = authorization[7:].strip()
        if not token or len(token) > 512:
            raise APIError(401, "authentication_required", "Provide a valid bearer token.")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        timestamp = now()
        with self.connect() as db:
            row = db.execute(
                """SELECT users.* FROM auth_tokens JOIN users ON users.id = auth_tokens.user_id
                   WHERE auth_tokens.token_hash = ? AND auth_tokens.expires_at > ?""",
                (token_hash, timestamp),
            ).fetchone()
            if row is None:
                db.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (token_hash,))
                raise APIError(401, "authentication_required", "Your session is invalid or has expired.")
        return dict(row)

    def optional_user(self, authorization: Optional[str]) -> Optional[dict[str, Any]]:
        if not authorization:
            return None
        return self.authenticate(authorization)

    def logout(self, authorization: Optional[str]) -> None:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise APIError(401, "authentication_required", "Provide a valid bearer token.")
        token = authorization[7:].strip()
        if not token:
            raise APIError(401, "authentication_required", "Provide a valid bearer token.")
        with self.connect() as db:
            db.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))

    @staticmethod
    def require_admin(user: dict[str, Any]) -> None:
        if user["role"] != "admin":
            raise APIError(403, "admin_required", "This action requires an administrator account.")

    def _course_row(self, db: sqlite3.Connection, course_id: int, user: Optional[dict[str, Any]] = None) -> sqlite3.Row:
        row = db.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        if row is None or (not row["published"] and (user is None or user["role"] != "admin")):
            raise APIError(404, "not_found", "The requested course was not found.")
        return row

    def serialize_course(self, db: sqlite3.Connection, row: sqlite3.Row, user: Optional[dict[str, Any]] = None, *, detail: bool = False) -> dict[str, Any]:
        lesson_count = int(db.execute("SELECT COUNT(*) FROM lessons WHERE course_id = ?", (row["id"],)).fetchone()[0])
        enrollment = None
        completed_count = 0
        if user is not None:
            enrollment = db.execute(
                "SELECT enrolled_at FROM enrollments WHERE user_id = ? AND course_id = ?", (user["id"], row["id"])
            ).fetchone()
            completed_count = int(
                db.execute(
                    """SELECT COUNT(*) FROM lesson_progress JOIN lessons ON lessons.id = lesson_progress.lesson_id
                       WHERE lesson_progress.user_id = ? AND lessons.course_id = ? AND lesson_progress.progress_percent >= 100""",
                    (user["id"], row["id"]),
                ).fetchone()[0]
            )
        progress_percent = round((completed_count / lesson_count) * 100, 1) if lesson_count else 0.0
        course: dict[str, Any] = {
            "id": row["id"],
            "title": row["title"],
            "summary": row["summary"],
            "category": row["category"],
            "thumbnail_url": row["thumbnail_url"],
            "published": bool(row["published"]),
            "lesson_count": lesson_count,
            "enrolled": enrollment is not None,
            "progress_percent": progress_percent if enrollment is not None else 0.0,
            "created_at": iso_timestamp(row["created_at"]),
            "updated_at": iso_timestamp(row["updated_at"]),
        }
        if detail:
            course["description"] = row["description"]
            course["enrolled_at"] = iso_timestamp(enrollment["enrolled_at"]) if enrollment else None
        return course

    def list_courses(self, user: Optional[dict[str, Any]], query: dict[str, list[str]]) -> list[dict[str, Any]]:
        search = query.get("q", [""])[0].strip()
        category = query.get("category", [""])[0].strip()
        mine = query.get("mine", [""])[0].lower() in ("1", "true")
        with self.connect() as db:
            sql = "SELECT courses.* FROM courses"
            conditions: list[str] = []
            parameters: list[Any] = []
            if mine:
                if user is None:
                    raise APIError(401, "authentication_required", "Sign in to view your courses.")
                sql += " JOIN enrollments ON enrollments.course_id = courses.id"
                conditions.append("enrollments.user_id = ?")
                parameters.append(user["id"])
            if user is None or user["role"] != "admin":
                conditions.append("courses.published = 1")
            if search:
                if len(search) > 100:
                    raise APIError(400, "invalid_input", "q must be at most 100 characters.")
                conditions.append("(courses.title LIKE ? OR courses.summary LIKE ? OR courses.category LIKE ?)")
                wildcard = f"%{search}%"
                parameters.extend((wildcard, wildcard, wildcard))
            if category:
                if len(category) > 60:
                    raise APIError(400, "invalid_input", "category must be at most 60 characters.")
                conditions.append("courses.category = ?")
                parameters.append(category)
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
            sql += " ORDER BY courses.updated_at DESC, courses.id DESC"
            rows = db.execute(sql, parameters).fetchall()
            return [self.serialize_course(db, row, user) for row in rows]

    def get_course(self, course_id: int, user: Optional[dict[str, Any]]) -> dict[str, Any]:
        with self.connect() as db:
            row = self._course_row(db, course_id, user)
            course = self.serialize_course(db, row, user, detail=True)
            lessons = db.execute("SELECT * FROM lessons WHERE course_id = ? ORDER BY position, id", (course_id,)).fetchall()
            course["lessons"] = [self.serialize_lesson(db, lesson, user) for lesson in lessons]
            return course

    def serialize_lesson(self, db: sqlite3.Connection, row: sqlite3.Row, user: Optional[dict[str, Any]] = None, *, detail: bool = False) -> dict[str, Any]:
        progress = None
        if user:
            progress = db.execute(
                "SELECT * FROM lesson_progress WHERE user_id = ? AND lesson_id = ?", (user["id"], row["id"])
            ).fetchone()
        quiz = db.execute("SELECT id FROM quizzes WHERE lesson_id = ?", (row["id"],)).fetchone()
        item: dict[str, Any] = {
            "id": row["id"],
            "course_id": row["course_id"],
            "title": row["title"],
            "description": row["description"],
            "video_url": row["video_url"],
            "duration_seconds": row["duration_seconds"],
            "position": row["position"],
            "quiz_id": quiz["id"] if quiz else None,
            "progress": self.serialize_progress(progress) if progress else None,
        }
        if detail:
            item["transcript"] = row["transcript"]
            item["created_at"] = iso_timestamp(row["created_at"])
            item["updated_at"] = iso_timestamp(row["updated_at"])
        return item

    @staticmethod
    def serialize_progress(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "lesson_id": row["lesson_id"],
            "progress_percent": float(row["progress_percent"]),
            "watched_seconds": row["watched_seconds"],
            "completed": float(row["progress_percent"]) >= 100,
            "completed_at": iso_timestamp(row["completed_at"]),
            "updated_at": iso_timestamp(row["updated_at"]),
        }

    def course_lessons(self, course_id: int, user: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
        with self.connect() as db:
            self._course_row(db, course_id, user)
            rows = db.execute("SELECT * FROM lessons WHERE course_id = ? ORDER BY position, id", (course_id,)).fetchall()
            return [self.serialize_lesson(db, row, user, detail=True) for row in rows]

    def get_lesson(self, lesson_id: int, user: Optional[dict[str, Any]]) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
            if row is None:
                raise APIError(404, "not_found", "The requested lesson was not found.")
            self._course_row(db, int(row["course_id"]), user)
            return self.serialize_lesson(db, row, user, detail=True)

    def _course_payload(self, data: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
        allowed = {"title", "summary", "description", "category", "thumbnail_url", "published"}
        unknown = set(data).difference(allowed)
        if unknown:
            raise APIError(400, "invalid_input", "The course contains unsupported fields.")
        output: dict[str, Any] = {}
        if "title" in data or not partial:
            output["title"] = clean_text(data.get("title"), "title", 3, 120)
        if "summary" in data or not partial:
            output["summary"] = clean_text(data.get("summary"), "summary", 10, 280)
        if "description" in data or not partial:
            output["description"] = clean_text(data.get("description"), "description", 20, 5000)
        if "category" in data or not partial:
            output["category"] = clean_text(data.get("category", "General"), "category", 2, 60)
        if "thumbnail_url" in data:
            output["thumbnail_url"] = clean_url(data.get("thumbnail_url"), "thumbnail_url")
        elif not partial:
            output["thumbnail_url"] = None
        if "published" in data:
            output["published"] = int(as_bool(data["published"], "published"))
        elif not partial:
            output["published"] = 0
        if partial and not output:
            raise APIError(400, "invalid_input", "Provide at least one course field to update.")
        return output

    def create_course(self, user: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        self.require_admin(user)
        payload = self._course_payload(data)
        timestamp = now()
        with self.connect() as db:
            cursor = db.execute(
                """INSERT INTO courses(title, summary, description, category, thumbnail_url, published, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    payload["title"], payload["summary"], payload["description"], payload["category"], payload["thumbnail_url"],
                    payload["published"], user["id"], timestamp, timestamp,
                ),
            )
            row = db.execute("SELECT * FROM courses WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self.serialize_course(db, row, user, detail=True)

    def update_course(self, course_id: int, user: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        self.require_admin(user)
        payload = self._course_payload(data, partial=True)
        with self.connect() as db:
            self._course_row(db, course_id, user)
            pieces = [f"{field} = ?" for field in payload]
            values = list(payload.values())
            pieces.append("updated_at = ?")
            values.extend((now(), course_id))
            db.execute(f"UPDATE courses SET {', '.join(pieces)} WHERE id = ?", values)
            row = db.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
            return self.serialize_course(db, row, user, detail=True)

    def _lesson_payload(self, data: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
        allowed = {"title", "description", "video_url", "duration_seconds", "position", "transcript"}
        if not partial and set(data).difference(allowed):
            raise APIError(400, "invalid_input", "The lesson contains unsupported fields.")
        payload: dict[str, Any] = {}
        if "title" in data or not partial:
            payload["title"] = clean_text(data.get("title"), "title", 3, 160)
        if "description" in data or not partial:
            payload["description"] = clean_text(data.get("description"), "description", 5, 3000)
        if "video_url" in data:
            payload["video_url"] = clean_url(data.get("video_url"), "video_url")
        elif not partial:
            payload["video_url"] = None
        if "duration_seconds" in data or not partial:
            candidate = data.get("duration_seconds", 0)
            if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0 or candidate > 86_400:
                raise APIError(400, "invalid_input", "duration_seconds must be a whole number between 0 and 86400.")
            payload["duration_seconds"] = candidate
        if "position" in data:
            candidate = data["position"]
            if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 1 or candidate > 10_000:
                raise APIError(400, "invalid_input", "position must be a whole number between 1 and 10000.")
            payload["position"] = candidate
        if "transcript" in data or not partial:
            payload["transcript"] = clean_text(data.get("transcript", ""), "transcript", 0, 20_000)
        if partial and not payload:
            raise APIError(400, "invalid_input", "Provide at least one lesson field to update.")
        return payload

    def create_lesson(self, course_id: int, user: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        self.require_admin(user)
        payload = self._lesson_payload(data)
        timestamp = now()
        with self.connect() as db:
            self._course_row(db, course_id, user)
            if "position" not in payload:
                payload["position"] = int(db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM lessons WHERE course_id = ?", (course_id,)).fetchone()[0])
            try:
                cursor = db.execute(
                    """INSERT INTO lessons(course_id, title, description, video_url, duration_seconds, position, transcript, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (course_id, payload["title"], payload["description"], payload["video_url"], payload["duration_seconds"], payload["position"], payload["transcript"], timestamp, timestamp),
                )
            except sqlite3.IntegrityError:
                raise APIError(409, "position_in_use", "Another lesson already uses that position.") from None
            row = db.execute("SELECT * FROM lessons WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self.serialize_lesson(db, row, user, detail=True)

    def enroll(self, course_id: int, user: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as db:
            row = self._course_row(db, course_id, user)
            db.execute(
                "INSERT INTO enrollments(user_id, course_id, enrolled_at) VALUES (?, ?, ?) ON CONFLICT(user_id, course_id) DO NOTHING",
                (user["id"], course_id, now()),
            )
            enrollment = db.execute("SELECT * FROM enrollments WHERE user_id = ? AND course_id = ?", (user["id"], course_id)).fetchone()
            course = self.serialize_course(db, row, user, detail=True)
            return {"course": course, "enrolled_at": iso_timestamp(enrollment["enrolled_at"])}

    def my_enrollments(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT courses.* FROM enrollments JOIN courses ON courses.id = enrollments.course_id
                   WHERE enrollments.user_id = ? ORDER BY enrollments.enrolled_at DESC""",
                (user["id"],),
            ).fetchall()
            return [self.serialize_course(db, row, user, detail=True) for row in rows]

    def _enrolled_lesson(self, db: sqlite3.Connection, user: dict[str, Any], lesson_id: int) -> sqlite3.Row:
        lesson = db.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
        if lesson is None:
            raise APIError(404, "not_found", "The requested lesson was not found.")
        enrolled = db.execute(
            "SELECT 1 FROM enrollments WHERE user_id = ? AND course_id = ?", (user["id"], lesson["course_id"])
        ).fetchone()
        if enrolled is None:
            raise APIError(403, "not_enrolled", "Enroll in this course before saving learning progress.")
        return lesson

    def save_progress(self, user: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        lesson_id = clean_identifier(data.get("lesson_id"), "lesson_id")
        candidate = data.get("progress_percent", data.get("progress"))
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)) or candidate < 0 or candidate > 100:
            raise APIError(400, "invalid_input", "progress_percent must be a number from 0 to 100.")
        progress_percent = round(float(candidate), 2)
        if "completed" in data and as_bool(data["completed"], "completed"):
            progress_percent = 100.0
        watched_seconds = data.get("watched_seconds", 0)
        if isinstance(watched_seconds, bool) or not isinstance(watched_seconds, int) or watched_seconds < 0 or watched_seconds > 86_400:
            raise APIError(400, "invalid_input", "watched_seconds must be a whole number from 0 to 86400.")
        timestamp = now()
        with self.connect() as db:
            self._enrolled_lesson(db, user, lesson_id)
            db.execute(
                """INSERT INTO lesson_progress(user_id, lesson_id, progress_percent, watched_seconds, completed_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, lesson_id) DO UPDATE SET
                     progress_percent = excluded.progress_percent,
                     watched_seconds = CASE WHEN excluded.watched_seconds > lesson_progress.watched_seconds THEN excluded.watched_seconds ELSE lesson_progress.watched_seconds END,
                     completed_at = CASE WHEN excluded.progress_percent >= 100 THEN COALESCE(lesson_progress.completed_at, excluded.completed_at) ELSE lesson_progress.completed_at END,
                     updated_at = excluded.updated_at""",
                (user["id"], lesson_id, progress_percent, watched_seconds, timestamp if progress_percent >= 100 else None, timestamp),
            )
            row = db.execute("SELECT * FROM lesson_progress WHERE user_id = ? AND lesson_id = ?", (user["id"], lesson_id)).fetchone()
            return self.serialize_progress(row)

    def progress(self, user: dict[str, Any], course_id: Optional[int] = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            sql = """SELECT lesson_progress.* FROM lesson_progress JOIN lessons ON lessons.id = lesson_progress.lesson_id
                     WHERE lesson_progress.user_id = ?"""
            args: list[Any] = [user["id"]]
            if course_id is not None:
                self._course_row(db, course_id, user)
                sql += " AND lessons.course_id = ?"
                args.append(course_id)
            sql += " ORDER BY lesson_progress.updated_at DESC"
            rows = db.execute(sql, args).fetchall()
            return [self.serialize_progress(row) for row in rows]

    @staticmethod
    def _questions(raw: str) -> list[dict[str, Any]]:
        try:
            questions = json.loads(raw)
        except json.JSONDecodeError:
            raise APIError(500, "data_error", "This quiz could not be loaded.") from None
        if not isinstance(questions, list):
            raise APIError(500, "data_error", "This quiz could not be loaded.")
        return questions

    @staticmethod
    def _public_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for question in questions:
            result.append({
                "id": question.get("id"),
                "question": question.get("question"),
                "options": question.get("options", []),
            })
        return result

    def _quiz_row(self, db: sqlite3.Connection, quiz_id: int, user: Optional[dict[str, Any]]) -> sqlite3.Row:
        row = db.execute(
            """SELECT quizzes.*, lessons.course_id FROM quizzes JOIN lessons ON lessons.id = quizzes.lesson_id
               WHERE quizzes.id = ?""",
            (quiz_id,),
        ).fetchone()
        if row is None:
            raise APIError(404, "not_found", "The requested quiz was not found.")
        self._course_row(db, int(row["course_id"]), user)
        return row

    def get_quiz(self, quiz_id: int, user: Optional[dict[str, Any]]) -> dict[str, Any]:
        with self.connect() as db:
            row = self._quiz_row(db, quiz_id, user)
            questions = self._questions(row["questions_json"])
            return {
                "id": row["id"],
                "lesson_id": row["lesson_id"],
                "course_id": row["course_id"],
                "title": row["title"],
                "questions": self._public_questions(questions),
            }

    def submit_quiz(self, quiz_id: int, user: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        submitted = data.get("answers")
        if isinstance(submitted, list):
            submitted = {str(index): value for index, value in enumerate(submitted)}
        if not isinstance(submitted, dict) or len(submitted) > 100:
            raise APIError(400, "invalid_input", "answers must be an object keyed by question id.")
        with self.connect() as db:
            row = self._quiz_row(db, quiz_id, user)
            self._enrolled_lesson(db, user, int(row["lesson_id"]))
            questions = self._questions(row["questions_json"])
            score = 0
            feedback: list[dict[str, Any]] = []
            normalized_answers: dict[str, str] = {}
            for index, question in enumerate(questions):
                question_id = str(question.get("id", index))
                answer = submitted.get(question_id, submitted.get(str(index), ""))
                answer_text = clean_text(answer, f"answers.{question_id}", 0, 500) if isinstance(answer, str) else ""
                normalized_answers[question_id] = answer_text
                # Hashing normalised Unicode avoids compare_digest's ASCII-only str limitation.
                answer_digest = hashlib.sha256(answer_text.casefold().encode("utf-8")).digest()
                expected_digest = hashlib.sha256(str(question.get("answer", "")).casefold().encode("utf-8")).digest()
                correct = hmac.compare_digest(answer_digest, expected_digest)
                score += int(correct)
                feedback.append({
                    "question_id": question_id,
                    "correct": correct,
                    "explanation": question.get("explanation", "Review this lesson and try again."),
                })
            timestamp = now()
            cursor = db.execute(
                """INSERT INTO quiz_attempts(user_id, quiz_id, answers_json, score, max_score, submitted_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user["id"], quiz_id, json.dumps(normalized_answers), score, len(questions), timestamp),
            )
            return {
                "id": cursor.lastrowid,
                "quiz_id": quiz_id,
                "score": score,
                "max_score": len(questions),
                "percentage": round((score / len(questions)) * 100, 1) if questions else 0.0,
                "submitted_at": iso_timestamp(timestamp),
                "feedback": feedback,
            }

    def tutor(self, user: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        question = clean_text(data.get("question", data.get("message")), "question", 3, 2000)
        context: list[str] = []
        course_id = data.get("course_id")
        lesson_id = data.get("lesson_id")
        with self.connect() as db:
            if course_id is not None:
                course = self._course_row(db, clean_identifier(course_id, "course_id"), user)
                context.append(f"Course: {course['title']}")
            if lesson_id is not None:
                lesson = db.execute("SELECT * FROM lessons WHERE id = ?", (clean_identifier(lesson_id, "lesson_id"),)).fetchone()
                if lesson is None:
                    raise APIError(404, "not_found", "The requested lesson was not found.")
                self._course_row(db, int(lesson["course_id"]), user)
                context.append(f"Lesson: {lesson['title']}")
                if lesson["transcript"]:
                    context.append("Lesson notes: " + lesson["transcript"][:3000])
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if api_key:
            try:
                reply = self._openai_tutor_reply(api_key, question, context)
                return {"reply": reply, "mode": "ai", "context": context[:2]}
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, KeyError, json.JSONDecodeError):
                # A configured integration should fail safely rather than expose an upstream error or block learning.
                pass
        return {"reply": self._guided_reply(question, context), "mode": "guided_fallback", "context": context[:2]}

    @staticmethod
    def _guided_reply(question: str, context: list[str]) -> str:
        subject = context[0].replace("Course: ", "the course ") if context else "this topic"
        return (
            f"Let’s work through your question about {subject}. Start by writing one sentence about what you already know, "
            f"then break the question into a smaller step. For “{question[:220]}”, review the relevant lesson notes, "
            "try an example in your own words, and tell me what part still feels unclear. I can help you check your reasoning without guessing beyond the learning material."
        )

    @staticmethod
    def _openai_tutor_reply(api_key: str, question: str, context: list[str]) -> str:
        """Call OpenAI only when an administrator intentionally supplies an API key."""
        instructions = (
            "You are a kind, concise learning coach for LearnWithAI. Help the learner reason step by step. "
            "Use the provided course context when available, clearly acknowledge uncertainty, and do not give medical, legal, or safety-critical instructions. "
            "Avoid requesting personal data."
        )
        if context:
            instructions += "\n\nLearning context:\n" + "\n".join(context)
        payload = json.dumps(
            {
                "model": os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
                "instructions": instructions,
                "input": question,
                "max_output_tokens": 500,
            }
        ).encode("utf-8")
        url = os.environ.get("OPENAI_API_URL", "https://api.openai.com/v1/responses")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "LearnWithAI/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310: URL is administrator configuration
            response_data = json.loads(response.read().decode("utf-8"))
        text = response_data.get("output_text")
        if isinstance(text, str) and text.strip():
            return text.strip()[:5000]
        for item in response_data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    return content["text"].strip()[:5000]
        raise ValueError("OpenAI response did not contain output text")


class LearnWithAIHandler(BaseHTTPRequestHandler):
    """HTTP adapter. It deliberately keeps routing explicit and auditable."""

    protocol_version = "HTTP/1.1"
    server_version = "LearnWithAI/1.0"

    @property
    def app(self) -> LearnWithAIApp:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        # Keep useful request information without printing credentials or bodies.
        if os.environ.get("LEARNWITHAI_ACCESS_LOG", "1") != "0":
            super().log_message(format, *args)

    def _origin_is_same(self) -> Optional[str]:
        origin = self.headers.get("Origin")
        if not origin:
            return None
        host = self.headers.get("Host", "")
        parsed = urllib.parse.urlparse(origin)
        if parsed.scheme in ("http", "https") and parsed.netloc == host and not parsed.path.rstrip("/") and not parsed.query and not parsed.fragment:
            return origin
        return None

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
            "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
            "media-src 'self' https: blob:; connect-src 'self'",
        )
        same_origin = self._origin_is_same()
        if same_origin:
            self.send_header("Access-Control-Allow-Origin", same_origin)
            self.send_header("Vary", "Origin")

    def _send_json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, error: APIError) -> None:
        self._send_json(error.status, {"error": {"code": error.code, "message": error.message}})

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type != "application/json":
            raise APIError(415, "json_required", "Content-Type must be application/json.")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise APIError(400, "invalid_request", "Content-Length is invalid.") from None
        if length < 1:
            raise APIError(400, "invalid_request", "A JSON request body is required.")
        if length > MAX_BODY_BYTES:
            raise APIError(413, "body_too_large", "The request body is too large.")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise APIError(400, "invalid_json", "The request body must contain valid JSON.") from None
        if not isinstance(value, dict):
            raise APIError(400, "invalid_json", "The JSON body must be an object.")
        return value

    def _user(self, required: bool = True) -> Optional[dict[str, Any]]:
        authorization = self.headers.get("Authorization")
        return self.app.authenticate(authorization) if required else self.app.optional_user(authorization)

    def _route(self) -> tuple[str, list[str], dict[str, list[str]]]:
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        parts = [piece for piece in path.split("/") if piece]
        return path, parts, urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

    def do_OPTIONS(self) -> None:  # noqa: N802
        same_origin = self._origin_is_same()
        if self.path.startswith("/api/") and same_origin:
            self.send_response(HTTPStatus.NO_CONTENT)
            self._security_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._security_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def _handle(self, method: str) -> None:
        try:
            path, parts, query = self._route()
            if path.startswith("/api/") or path == "/api":
                self._handle_api(method, parts, query)
            elif method in ("GET", "HEAD"):
                self._serve_static(path)
            else:
                raise APIError(405, "method_not_allowed", "This method is not allowed for static files.")
        except APIError as error:
            self._error(error)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            # Do not leak implementation details. Development logs still retain a traceback through the server runner.
            self._send_json(500, {"error": {"code": "internal_error", "message": "An unexpected server error occurred."}})

    def _handle_api(self, method: str, parts: list[str], query: dict[str, list[str]]) -> None:
        if parts == ["api", "health"] and method in ("GET", "HEAD"):
            with self.app.connect() as db:
                db.execute("SELECT 1").fetchone()
            self._send_json(200, {"status": "ok", "service": "learnwithai", "version": APP_VERSION, "time": iso_timestamp(now())})
            return

        if parts == ["api", "auth", "register"] and method == "POST":
            user, token = self.app.register(self._read_json())
            self._send_json(201, {"user": user, "token": token, "token_type": "Bearer", "expires_in": self.app.token_ttl_seconds})
            return
        if parts == ["api", "auth", "login"] and method == "POST":
            user, token = self.app.login(self._read_json())
            self._send_json(200, {"user": user, "token": token, "token_type": "Bearer", "expires_in": self.app.token_ttl_seconds})
            return
        if parts == ["api", "auth", "logout"] and method == "POST":
            self.app.logout(self.headers.get("Authorization"))
            self._send_json(200, {"ok": True})
            return
        if parts in (["api", "auth", "me"], ["api", "me"]) and method in ("GET", "HEAD"):
            user = self._user()
            self._send_json(200, {"user": public_user(user)})
            return

        if parts == ["api", "courses"] and method in ("GET", "HEAD"):
            user = self._user(required=False)
            self._send_json(200, {"courses": self.app.list_courses(user, query)})
            return
        if parts == ["api", "courses"] and method == "POST":
            user = self._user()
            self._send_json(201, {"course": self.app.create_course(user, self._read_json())})
            return
        if parts == ["api", "admin", "courses"] and method in ("GET", "HEAD"):
            user = self._user()
            self.app.require_admin(user)
            self._send_json(200, {"courses": self.app.list_courses(user, query)})
            return
        if parts == ["api", "admin", "courses"] and method == "POST":
            user = self._user()
            self._send_json(201, {"course": self.app.create_course(user, self._read_json())})
            return
        if len(parts) == 3 and parts[:2] == ["api", "courses"]:
            course_id = clean_identifier(parts[2])
            if method in ("GET", "HEAD"):
                user = self._user(required=False)
                self._send_json(200, {"course": self.app.get_course(course_id, user)})
                return
            if method == "PATCH":
                user = self._user()
                self._send_json(200, {"course": self.app.update_course(course_id, user, self._read_json())})
                return
        if len(parts) == 4 and parts[:2] == ["api", "courses"] and parts[3] == "lessons":
            course_id = clean_identifier(parts[2])
            if method in ("GET", "HEAD"):
                user = self._user(required=False)
                self._send_json(200, {"lessons": self.app.course_lessons(course_id, user)})
                return
            if method == "POST":
                user = self._user()
                self._send_json(201, {"lesson": self.app.create_lesson(course_id, user, self._read_json())})
                return
        if len(parts) == 4 and parts[:2] == ["api", "courses"] and parts[3] == "enroll" and method == "POST":
            user = self._user()
            self._send_json(200, {"enrollment": self.app.enroll(clean_identifier(parts[2]), user)})
            return
        if len(parts) == 3 and parts[:2] == ["api", "lessons"] and method in ("GET", "HEAD"):
            user = self._user(required=False)
            self._send_json(200, {"lesson": self.app.get_lesson(clean_identifier(parts[2]), user)})
            return

        if parts == ["api", "me", "enrollments"] and method in ("GET", "HEAD"):
            user = self._user()
            self._send_json(200, {"enrollments": self.app.my_enrollments(user)})
            return
        if parts == ["api", "progress"] and method in ("GET", "HEAD"):
            user = self._user()
            raw_course_id = query.get("course_id", [None])[0]
            course_id = clean_identifier(raw_course_id, "course_id") if raw_course_id not in (None, "") else None
            self._send_json(200, {"progress": self.app.progress(user, course_id)})
            return
        if parts == ["api", "progress"] and method == "POST":
            user = self._user()
            self._send_json(200, {"progress": self.app.save_progress(user, self._read_json())})
            return

        if len(parts) == 3 and parts[:2] == ["api", "quizzes"] and method in ("GET", "HEAD"):
            user = self._user(required=False)
            self._send_json(200, {"quiz": self.app.get_quiz(clean_identifier(parts[2]), user)})
            return
        if len(parts) == 4 and parts[:2] == ["api", "quizzes"] and parts[3] == "submit" and method == "POST":
            user = self._user()
            self._send_json(201, {"attempt": self.app.submit_quiz(clean_identifier(parts[2]), user, self._read_json())})
            return
        if parts == ["api", "tutor"] and method == "POST":
            user = self._user()
            self._send_json(200, self.app.tutor(user, self._read_json()))
            return

        raise APIError(404, "not_found", "The requested API endpoint was not found.")

    def _serve_static(self, request_path: str) -> None:
        web_root = self.app.web_root.resolve()
        if not web_root.is_dir():
            raise APIError(404, "not_found", "The web application has not been built yet.")
        relative = request_path.lstrip("/") or "index.html"
        requested = (web_root / relative).resolve()
        try:
            requested.relative_to(web_root)
        except ValueError:
            raise APIError(404, "not_found", "The requested file was not found.") from None
        # A client-side route gets the SPA entry point, but missing assets remain a real 404.
        if not requested.is_file() and "." not in Path(relative).name:
            requested = web_root / "index.html"
        if not requested.is_file():
            raise APIError(404, "not_found", "The requested file was not found.")
        content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        data = requested.read_bytes()
        self.send_response(200)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        # Keep application shell assets fresh; versioned media can be cached by the chosen video/CDN provider.
        self.send_header("Cache-Control", "no-cache" if requested.suffix in {".html", ".js", ".css"} else "public, max-age=3600")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


class LearnWithAIHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: LearnWithAIApp) -> None:
        self.app = app
        super().__init__(address, LearnWithAIHandler)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    database_path: str | Path | None = None,
    web_root: str | Path | None = None,
    token_ttl_seconds: Optional[int] = None,
) -> LearnWithAIHTTPServer:
    """Factory used by both the command line and the stdlib integration tests."""
    return LearnWithAIHTTPServer((host, port), LearnWithAIApp(database_path, web_root, token_ttl_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LearnWithAI web and API server.")
    parser.add_argument("--host", default=os.environ.get("LEARNWITHAI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LEARNWITHAI_PORT", "8000")))
    parser.add_argument("--db", default=os.environ.get("LEARNWITHAI_DB"))
    parser.add_argument("--web-root", default=os.environ.get("LEARNWITHAI_WEB_ROOT"))
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.db, args.web_root)
    print(f"{APP_NAME} is running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping LearnWithAI.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
