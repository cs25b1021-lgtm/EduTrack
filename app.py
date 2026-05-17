from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "student_records_v2.sqlite3"
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
SESSION_COOKIE = "sarams_session"
SESSION_DAYS = 7
ATTENDANCE_EDIT_DAYS = 7
IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,30}$")
PAGE_SIZE = 25


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def today_iso() -> str:
    return date.today().isoformat()


def dict_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return f"{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_b64, digest_b64 = stored.split("$", 1)
        salt = base64.b64decode(salt_b64.encode())
        expected = base64.b64decode(digest_b64.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def grade_for(marks: float, max_marks: float) -> str:
    if max_marks <= 0:
        return "N/A"
    pct = (marks / max_marks) * 100
    if pct >= 90:
        return "O"
    if pct >= 80:
        return "A+"
    if pct >= 70:
        return "A"
    if pct >= 60:
        return "B+"
    if pct >= 50:
        return "B"
    if pct >= 40:
        return "C"
    return "F"


def normalized_identifier(value: str, label: str) -> str:
    identifier = value.strip().lower()
    if not IDENTIFIER_RE.match(identifier):
        raise HttpError(400, f"{label} may contain only letters, numbers, dot, underscore, and hyphen.")
    return identifier


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL CHECK (role IN ('ADMIN','TEACHER','STUDENT')),
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('PENDING','APPROVED','REJECTED','INACTIVE')),
            photo_data TEXT,
            created_at TEXT NOT NULL,
            last_login_at TEXT
        );

        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            code TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            code TEXT NOT NULL UNIQUE,
            duration_semesters INTEGER NOT NULL DEFAULT 8,
            FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            semester INTEGER NOT NULL,
            name TEXT NOT NULL,
            capacity INTEGER NOT NULL DEFAULT 60,
            FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE CASCADE,
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            semester INTEGER NOT NULL,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            credit INTEGER NOT NULL DEFAULT 4,
            attendance_required REAL NOT NULL DEFAULT 75,
            FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE CASCADE,
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS teacher_profiles (
            user_id INTEGER PRIMARY KEY,
            employee_id TEXT NOT NULL UNIQUE,
            department_id INTEGER,
            title TEXT DEFAULT 'Assistant Professor',
            phone TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS student_profiles (
            user_id INTEGER PRIMARY KEY,
            roll_number TEXT NOT NULL UNIQUE,
            department_id INTEGER,
            course_id INTEGER,
            section_id INTEGER,
            semester INTEGER NOT NULL DEFAULT 1,
            phone TEXT,
            guardian_name TEXT,
            academic_status TEXT NOT NULL DEFAULT 'ACTIVE'
                CHECK (academic_status IN ('ACTIVE','INACTIVE','DETAINED','NOT_ELIGIBLE')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE RESTRICT,
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE RESTRICT,
            FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS teacher_subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            section_id INTEGER NOT NULL,
            FOREIGN KEY (teacher_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
            FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE,
            UNIQUE (teacher_id, subject_id, section_id)
        );

        CREATE TABLE IF NOT EXISTS enrollments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            section_id INTEGER NOT NULL,
            FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
            FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE,
            UNIQUE (student_id, subject_id, section_id)
        );

        CREATE TABLE IF NOT EXISTS exams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            exam_type TEXT NOT NULL,
            max_marks REAL NOT NULL,
            weight REAL NOT NULL DEFAULT 1,
            exam_date TEXT,
            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS attendance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            section_id INTEGER NOT NULL,
            teacher_id INTEGER NOT NULL,
            attendance_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('Present','Absent','On Leave')),
            reason TEXT,
            student_absence_reason TEXT,
            absence_reason_updated_at TEXT,
            correction_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
            FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE,
            FOREIGN KEY (teacher_id) REFERENCES users(id) ON DELETE RESTRICT,
            UNIQUE (student_id, subject_id, attendance_date)
        );

        CREATE TABLE IF NOT EXISTS marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            exam_id INTEGER NOT NULL,
            teacher_id INTEGER NOT NULL,
            marks_obtained REAL NOT NULL,
            grade TEXT NOT NULL,
            remarks TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
            FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
            FOREIGN KEY (teacher_id) REFERENCES users(id) ON DELETE RESTRICT,
            UNIQUE (student_id, exam_id)
        );

        CREATE TABLE IF NOT EXISTS notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            audience TEXT NOT NULL DEFAULT 'ALL',
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER,
            action TEXT NOT NULL,
            entity TEXT,
            entity_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (actor_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()


def table_columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    return {row["name"]: row for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def rebuild_student_profiles_if_needed(conn: sqlite3.Connection) -> None:
    columns = table_columns(conn, "student_profiles")
    if not columns:
        return
    needs_rebuild = any(columns[name]["notnull"] for name in ("department_id", "course_id", "section_id") if name in columns)
    if not needs_rebuild:
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        ALTER TABLE student_profiles RENAME TO student_profiles_old;
        CREATE TABLE student_profiles (
            user_id INTEGER PRIMARY KEY,
            roll_number TEXT NOT NULL UNIQUE,
            department_id INTEGER,
            course_id INTEGER,
            section_id INTEGER,
            semester INTEGER NOT NULL DEFAULT 1,
            phone TEXT,
            guardian_name TEXT,
            academic_status TEXT NOT NULL DEFAULT 'ACTIVE'
                CHECK (academic_status IN ('ACTIVE','INACTIVE','DETAINED','NOT_ELIGIBLE')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE RESTRICT,
            FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE RESTRICT,
            FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE RESTRICT
        );
        INSERT INTO student_profiles
            (user_id, roll_number, department_id, course_id, section_id, semester, phone, guardian_name, academic_status)
        SELECT user_id, roll_number, department_id, course_id, section_id, semester, phone, guardian_name, academic_status
        FROM student_profiles_old;
        DROP TABLE student_profiles_old;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def rebuild_teacher_profiles_if_needed(conn: sqlite3.Connection) -> None:
    columns = table_columns(conn, "teacher_profiles")
    if not columns or not columns.get("department_id") or not columns["department_id"]["notnull"]:
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        ALTER TABLE teacher_profiles RENAME TO teacher_profiles_old;
        CREATE TABLE teacher_profiles (
            user_id INTEGER PRIMARY KEY,
            employee_id TEXT NOT NULL UNIQUE,
            department_id INTEGER,
            title TEXT DEFAULT 'Assistant Professor',
            phone TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE RESTRICT
        );
        INSERT INTO teacher_profiles (user_id, employee_id, department_id, title, phone)
        SELECT user_id, employee_id, department_id, title, phone
        FROM teacher_profiles_old;
        DROP TABLE teacher_profiles_old;
        """
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def migrate_database(conn: sqlite3.Connection) -> None:
    user_columns = table_columns(conn, "users")
    if "photo_data" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN photo_data TEXT")
    rebuild_student_profiles_if_needed(conn)
    rebuild_teacher_profiles_if_needed(conn)
    attendance_columns = table_columns(conn, "attendance_records")
    if "student_absence_reason" not in attendance_columns:
        conn.execute("ALTER TABLE attendance_records ADD COLUMN student_absence_reason TEXT")
    if "absence_reason_updated_at" not in attendance_columns:
        conn.execute("ALTER TABLE attendance_records ADD COLUMN absence_reason_updated_at TEXT")
    conn.commit()


def insert_user(
    conn: sqlite3.Connection,
    role: str,
    name: str,
    email: str,
    password: str,
    status: str = "PENDING",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO users (role, name, email, password_hash, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (role, name, email.lower().strip(), hash_password(password), status, now_iso()),
    )
    return int(cur.lastrowid)


def seed_database(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
    if existing:
        admin = conn.execute("SELECT id, password_hash FROM users WHERE email = ?", ("admin@college.local",)).fetchone()
        if admin and not verify_password("admin123", admin["password_hash"]):
            conn.execute(
                "UPDATE users SET password_hash = ?, status = 'APPROVED' WHERE id = ?",
                (hash_password("admin123"), admin["id"]),
            )
            conn.commit()
        return

    admin = conn.execute(
    "SELECT id FROM users WHERE email = ?",
    ("admin@college.local",)
).fetchone()["id"]
    conn.execute(
        "INSERT INTO activity_logs (actor_id, action, entity, entity_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (admin, "Created clean administrator account", "system", None, now_iso()),
    )
    conn.commit()
    

    cse = conn.execute(
        "INSERT INTO departments (name, code) VALUES (?, ?)",
        ("Computer Science Engineering", "CSE"),
    ).lastrowid
    ece = conn.execute(
        "INSERT INTO departments (name, code) VALUES (?, ?)",
        ("Electronics and Communication", "ECE"),
    ).lastrowid

    btech = conn.execute(
        "INSERT INTO courses (department_id, name, code, duration_semesters) VALUES (?, ?, ?, ?)",
        (cse, "B.Tech Computer Science", "BTECH-CSE", 8),
    ).lastrowid
    bca = conn.execute(
        "INSERT INTO courses (department_id, name, code, duration_semesters) VALUES (?, ?, ?, ?)",
        (cse, "Bachelor of Computer Applications", "BCA", 6),
    ).lastrowid
    ece_course = conn.execute(
        "INSERT INTO courses (department_id, name, code, duration_semesters) VALUES (?, ?, ?, ?)",
        (ece, "B.Tech Electronics", "BTECH-ECE", 8),
    ).lastrowid

    cse_a = conn.execute(
        """
        INSERT INTO sections (department_id, course_id, semester, name, capacity)
        VALUES (?, ?, ?, ?, ?)
        """,
        (cse, btech, 3, "CSE-A", 60),
    ).lastrowid
    cse_bca = conn.execute(
        """
        INSERT INTO sections (department_id, course_id, semester, name, capacity)
        VALUES (?, ?, ?, ?, ?)
        """,
        (cse, bca, 1, "BCA-A", 50),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO sections (department_id, course_id, semester, name, capacity)
        VALUES (?, ?, ?, ?, ?)
        """,
        (ece, ece_course, 3, "ECE-A", 55),
    )

    dbms = conn.execute(
        """
        INSERT INTO subjects (department_id, course_id, semester, code, name, credit, attendance_required)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (cse, btech, 3, "CS301", "Database Management Systems", 4, 75),
    ).lastrowid
    oop = conn.execute(
        """
        INSERT INTO subjects (department_id, course_id, semester, code, name, credit, attendance_required)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (cse, btech, 3, "CS302", "Object Oriented Programming", 4, 75),
    ).lastrowid
    maths = conn.execute(
        """
        INSERT INTO subjects (department_id, course_id, semester, code, name, credit, attendance_required)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (cse, btech, 3, "MA301", "Discrete Mathematics", 3, 75),
    ).lastrowid
    web = conn.execute(
        """
        INSERT INTO subjects (department_id, course_id, semester, code, name, credit, attendance_required)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (cse, bca, 1, "BCA101", "Web Fundamentals", 4, 75),
    ).lastrowid

    admin = insert_user(conn, "ADMIN", "Academic Admin", "admin@college.local", "admin123", "APPROVED")
    teacher_ravi = insert_user(conn, "TEACHER", "Dr. Ravi Sharma", "ravi@college.local", "teacher123", "APPROVED")
    teacher_priya = insert_user(conn, "TEACHER", "Prof. Priya Nair", "priya@college.local", "teacher123", "APPROVED")
    pending_teacher = insert_user(conn, "TEACHER", "Neha Verma", "neha@college.local", "teacher123", "PENDING")

    conn.execute(
        "INSERT INTO teacher_profiles (user_id, employee_id, department_id, title, phone) VALUES (?, ?, ?, ?, ?)",
        (teacher_ravi, "T-CSE-001", cse, "Associate Professor", "9876500001"),
    )
    conn.execute(
        "INSERT INTO teacher_profiles (user_id, employee_id, department_id, title, phone) VALUES (?, ?, ?, ?, ?)",
        (teacher_priya, "T-CSE-002", cse, "Assistant Professor", "9876500002"),
    )
    conn.execute(
        "INSERT INTO teacher_profiles (user_id, employee_id, department_id, title, phone) VALUES (?, ?, ?, ?, ?)",
        (pending_teacher, "T-CSE-003", cse, "Assistant Professor", "9876500003"),
    )

    student_rows = [
        ("Ananya Rao", "ananya@college.local", "student123", "CSE-2024-031", "9876500101", "Mohan Rao"),
        ("Karan Mehta", "karan@college.local", "student123", "CSE-2024-032", "9876500102", "Sonia Mehta"),
        ("Farhan Ali", "farhan@college.local", "student123", "CSE-2024-033", "9876500103", "Aamir Ali"),
        ("Meera Iyer", "meera@college.local", "student123", "CSE-2024-034", "9876500104", "Lakshmi Iyer"),
    ]
    student_ids: list[int] = []
    for name, email, password, roll, phone, guardian in student_rows:
        student_id = insert_user(conn, "STUDENT", name, email, password, "APPROVED")
        student_ids.append(student_id)
        conn.execute(
            """
            INSERT INTO student_profiles
                (user_id, roll_number, department_id, course_id, section_id, semester, phone, guardian_name, academic_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (student_id, roll, cse, btech, cse_a, 3, phone, guardian, "ACTIVE"),
        )

    pending_student = insert_user(
        conn,
        "STUDENT",
        "Devika Sen",
        "devika@college.local",
        "student123",
        "PENDING",
    )
    conn.execute(
        """
        INSERT INTO student_profiles
            (user_id, roll_number, department_id, course_id, section_id, semester, phone, guardian_name, academic_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (pending_student, "CSE-2024-040", cse, btech, cse_a, 3, "9876500140", "Arun Sen", "ACTIVE"),
    )

    bca_student = insert_user(conn, "STUDENT", "Ritu Das", "ritu@college.local", "student123", "APPROVED")
    conn.execute(
        """
        INSERT INTO student_profiles
            (user_id, roll_number, department_id, course_id, section_id, semester, phone, guardian_name, academic_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (bca_student, "BCA-2026-011", cse, bca, cse_bca, 1, "9876500111", "Nikhil Das", "ACTIVE"),
    )

    for subject_id in (dbms, oop):
        conn.execute(
            "INSERT INTO teacher_subjects (teacher_id, subject_id, section_id) VALUES (?, ?, ?)",
            (teacher_ravi, subject_id, cse_a),
        )
    conn.execute(
        "INSERT INTO teacher_subjects (teacher_id, subject_id, section_id) VALUES (?, ?, ?)",
        (teacher_priya, maths, cse_a),
    )
    conn.execute(
        "INSERT INTO teacher_subjects (teacher_id, subject_id, section_id) VALUES (?, ?, ?)",
        (teacher_priya, web, cse_bca),
    )

    for student_id in student_ids:
        for subject_id in (dbms, oop, maths):
            conn.execute(
                "INSERT INTO enrollments (student_id, subject_id, section_id) VALUES (?, ?, ?)",
                (student_id, subject_id, cse_a),
            )
    conn.execute(
        "INSERT INTO enrollments (student_id, subject_id, section_id) VALUES (?, ?, ?)",
        (bca_student, web, cse_bca),
    )

    exam_ids: dict[tuple[int, str], int] = {}
    for subject_id in (dbms, oop, maths, web):
        for name, exam_type, max_marks, days_ahead in [
            ("Internal Test 1", "INTERNAL", 20, -12),
            ("Midterm", "MID", 50, -4),
            ("Assignment", "ASSIGNMENT", 10, 6),
            ("Final Exam", "FINAL", 100, 35),
        ]:
            exam_id = conn.execute(
                """
                INSERT INTO exams (subject_id, name, exam_type, max_marks, weight, exam_date)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (subject_id, name, exam_type, max_marks, 1, (date.today() + timedelta(days=days_ahead)).isoformat()),
            ).lastrowid
            exam_ids[(subject_id, name)] = exam_id

    attendance_dates = [date.today() - timedelta(days=i) for i in range(1, 12)]
    attendance_dates = [d for d in attendance_dates if d.weekday() < 5][:8]
    for d in attendance_dates:
        for subject_id, teacher_id in [(dbms, teacher_ravi), (oop, teacher_ravi), (maths, teacher_priya)]:
            for index, student_id in enumerate(student_ids):
                if student_id == student_ids[2] and subject_id == dbms and index % 2 == 0:
                    status = "Absent"
                elif student_id == student_ids[1] and d.day % 5 == 0:
                    status = "On Leave"
                elif student_id == student_ids[3] and subject_id == maths and d.day % 3 == 0:
                    status = "Absent"
                else:
                    status = "Present"
                conn.execute(
                    """
                    INSERT INTO attendance_records
                        (student_id, subject_id, section_id, teacher_id, attendance_date, status, reason,
                         correction_reason, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        student_id,
                        subject_id,
                        cse_a,
                        teacher_id,
                        d.isoformat(),
                        status,
                        "Medical" if status == "On Leave" else "",
                        "",
                        now_iso(),
                        now_iso(),
                    ),
                )

    marks_seed = [
        (dbms, "Internal Test 1", [18, 16, 9, 19], teacher_ravi),
        (dbms, "Midterm", [43, 39, 21, 46], teacher_ravi),
        (oop, "Internal Test 1", [17, 15, 14, 19], teacher_ravi),
        (oop, "Midterm", [41, 37, 31, 45], teacher_ravi),
        (maths, "Internal Test 1", [16, 12, 10, 18], teacher_priya),
        (maths, "Midterm", [38, 28, 22, 44], teacher_priya),
    ]
    for subject_id, exam_name, marks_list, teacher_id in marks_seed:
        exam = conn.execute("SELECT id, max_marks FROM exams WHERE subject_id = ? AND name = ?", (subject_id, exam_name)).fetchone()
        for student_id, score in zip(student_ids, marks_list):
            conn.execute(
                """
                INSERT INTO marks
                    (student_id, subject_id, exam_id, teacher_id, marks_obtained, grade, remarks, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    student_id,
                    subject_id,
                    exam["id"],
                    teacher_id,
                    score,
                    grade_for(float(score), float(exam["max_marks"])),
                    "",
                    now_iso(),
                    now_iso(),
                ),
            )

    conn.execute(
        "INSERT INTO notices (title, message, audience, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            "Internal marks review",
            "Teachers must finish internal marks review before the final submission window closes.",
            "TEACHER",
            admin,
            now_iso(),
        ),
    )
    conn.execute(
        "INSERT INTO notices (title, message, audience, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            "Attendance warning threshold",
            "Students below 75 percent attendance in any subject should meet their class advisor this week.",
            "STUDENT",
            admin,
            now_iso(),
        ),
    )
    conn.execute(
        "INSERT INTO activity_logs (actor_id, action, entity, entity_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (admin, "Seeded initial academic data", "system", None, now_iso()),
    )
    conn.commit()


def init_database() -> None:
    with connect() as conn:
        create_schema(conn)
        migrate_database(conn)
        seed_database(conn)


def log_activity(conn: sqlite3.Connection, actor_id: int | None, action: str, entity: str = "", entity_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO activity_logs (actor_id, action, entity, entity_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (actor_id, action, entity, entity_id, now_iso()),
    )


def get_user_by_session(conn: sqlite3.Connection, token: str | None) -> dict | None:
    if not token:
        return None
    row = conn.execute(
        """
        SELECT u.id, u.role, u.name, u.email, u.status
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > ? AND u.status = 'APPROVED'
        """,
        (token, now_iso()),
    ).fetchone()
    return dict_row(row)


def public_bootstrap(conn: sqlite3.Connection) -> dict:
    departments = conn.execute("SELECT id, name, code FROM departments ORDER BY name").fetchall()
    courses = conn.execute(
        """
        SELECT c.id, c.name, c.code, c.department_id AS departmentId, d.name AS departmentName
        FROM courses c
        JOIN departments d ON d.id = c.department_id
        ORDER BY c.name
        """
    ).fetchall()
    sections = conn.execute(
        """
        SELECT s.id, s.name, s.semester, s.capacity, s.department_id AS departmentId, s.course_id AS courseId,
               d.name AS departmentName, c.name AS courseName
        FROM sections s
        JOIN departments d ON d.id = s.department_id
        JOIN courses c ON c.id = s.course_id
        ORDER BY s.name
        """
    ).fetchall()
    return {
        "departments": [dict_row(r) for r in departments],
        "courses": [dict_row(r) for r in courses],
        "sections": [dict_row(r) for r in sections],
        "roles": ["STUDENT", "TEACHER"],
        "today": today_iso(),
    }


def full_user(conn: sqlite3.Connection, user_id: int) -> dict:
    user = dict_row(
        conn.execute(
            "SELECT id, role, name, email, status, photo_data AS photoData, created_at AS createdAt, last_login_at AS lastLoginAt FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    )
    if not user:
        raise HttpError(404, "User not found.")

    if user["role"] == "STUDENT":
        profile = dict_row(
            conn.execute(
                """
                SELECT sp.roll_number AS rollNumber, sp.semester, sp.phone, sp.guardian_name AS guardianName,
                       sp.academic_status AS academicStatus, sp.department_id AS departmentId,
                       sp.course_id AS courseId, sp.section_id AS sectionId,
                       d.name AS departmentName, c.name AS courseName, sec.name AS sectionName
                FROM student_profiles sp
                LEFT JOIN departments d ON d.id = sp.department_id
                LEFT JOIN courses c ON c.id = sp.course_id
                LEFT JOIN sections sec ON sec.id = sp.section_id
                WHERE sp.user_id = ?
                """,
                (user_id,),
            ).fetchone()
        )
    elif user["role"] == "TEACHER":
        profile = dict_row(
            conn.execute(
                """
                SELECT tp.employee_id AS employeeId, tp.title, tp.phone, tp.department_id AS departmentId,
                       d.name AS departmentName
                FROM teacher_profiles tp
                LEFT JOIN departments d ON d.id = tp.department_id
                WHERE tp.user_id = ?
                """,
                (user_id,),
            ).fetchone()
        )
    else:
        profile = {}
    user["profile"] = profile or {}
    return user


def attendance_summary_for_student(conn: sqlite3.Connection, student_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT sub.id AS subjectId, sub.code, sub.name, sub.attendance_required AS required,
               COUNT(ar.id) AS totalClasses,
               SUM(CASE WHEN ar.status = 'Present' THEN 1 ELSE 0 END) AS presentClasses,
               SUM(CASE WHEN ar.status = 'Absent' THEN 1 ELSE 0 END) AS absentClasses,
               SUM(CASE WHEN ar.status = 'On Leave' THEN 1 ELSE 0 END) AS leaveClasses
        FROM enrollments e
        JOIN subjects sub ON sub.id = e.subject_id
        LEFT JOIN attendance_records ar ON ar.student_id = e.student_id AND ar.subject_id = e.subject_id
        WHERE e.student_id = ?
        GROUP BY sub.id
        ORDER BY sub.code
        """,
        (student_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict_row(row)
        total = item["totalClasses"] or 0
        present = item["presentClasses"] or 0
        item["percentage"] = round((present / total) * 100, 2) if total else 0
        item["atRisk"] = total > 0 and item["percentage"] < item["required"]
        result.append(item)
    return result


def marks_for_student(conn: sqlite3.Connection, student_id: int, limit: int | None = None, offset: int = 0) -> list[dict]:
    paging_sql = "LIMIT ? OFFSET ?" if limit is not None else ""
    params: list[object] = [student_id]
    if limit is not None:
        params.extend([limit, offset])
    rows = conn.execute(
        f"""
        SELECT sub.id AS subjectId, sub.code, sub.name AS subjectName,
               e.id AS examId, e.name AS examName, e.exam_type AS examType, e.max_marks AS maxMarks,
               e.exam_date AS examDate, m.marks_obtained AS marksObtained, m.grade, m.remarks
        FROM enrollments en
        JOIN subjects sub ON sub.id = en.subject_id
        JOIN exams e ON e.subject_id = sub.id
        LEFT JOIN marks m ON m.exam_id = e.id AND m.student_id = en.student_id
        WHERE en.student_id = ?
        ORDER BY sub.code, e.exam_date, e.id
        {paging_sql}
        """,
        params,
    ).fetchall()
    return [dict_row(r) for r in rows]


def latest_notices(conn: sqlite3.Connection, role: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT n.id, n.title, n.message, n.audience, n.created_at AS createdAt, u.name AS createdBy
        FROM notices n
        JOIN users u ON u.id = n.created_by
        WHERE n.audience IN ('ALL', ?)
        ORDER BY n.created_at DESC, n.id DESC
        LIMIT 8
        """,
        (role,),
    ).fetchall()
    return [dict_row(r) for r in rows]


def teacher_assignments(conn: sqlite3.Connection, teacher_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT ts.id AS assignmentId, ts.teacher_id AS teacherId, ts.subject_id AS subjectId,
               ts.section_id AS sectionId, sub.code, sub.name AS subjectName,
               sub.attendance_required AS attendanceRequired, sec.name AS sectionName,
               sec.semester, d.name AS departmentName, c.name AS courseName,
               COUNT(en.id) AS studentCount
        FROM teacher_subjects ts
        JOIN subjects sub ON sub.id = ts.subject_id
        JOIN sections sec ON sec.id = ts.section_id
        JOIN departments d ON d.id = sec.department_id
        JOIN courses c ON c.id = sec.course_id
        LEFT JOIN enrollments en ON en.subject_id = ts.subject_id AND en.section_id = ts.section_id
        WHERE ts.teacher_id = ?
        GROUP BY ts.id
        ORDER BY sec.name, sub.code
        """,
        (teacher_id,),
    ).fetchall()
    return [dict_row(r) for r in rows]


def teacher_can_access(conn: sqlite3.Connection, teacher_id: int, subject_id: int, section_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM teacher_subjects
        WHERE teacher_id = ? AND subject_id = ? AND section_id = ?
        """,
        (teacher_id, subject_id, section_id),
    ).fetchone()
    return row is not None


def class_roster(conn: sqlite3.Connection, subject_id: int, section_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT u.id AS studentId, u.name, u.email, sp.roll_number AS rollNumber,
               sp.academic_status AS academicStatus
        FROM enrollments e
        JOIN users u ON u.id = e.student_id
        JOIN student_profiles sp ON sp.user_id = u.id
        WHERE e.subject_id = ? AND e.section_id = ? AND u.status = 'APPROVED'
        ORDER BY sp.roll_number
        """,
        (subject_id, section_id),
    ).fetchall()
    return [dict_row(r) for r in rows]


def attendance_analytics(
    conn: sqlite3.Connection,
    subject_id: int | None = None,
    section_id: int | None = None,
    teacher_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    join_params: list[object] = []
    where_params: list[object] = []
    paging_params: list[object] = []
    paging_sql = ""
    where = ["u.status = 'APPROVED'"]
    attendance_join = ["ar.student_id = e.student_id", "ar.subject_id = e.subject_id"]
    if date_from:
        attendance_join.append("ar.attendance_date >= ?")
        join_params.append(date_from)
    if date_to:
        attendance_join.append("ar.attendance_date <= ?")
        join_params.append(date_to)
    if subject_id:
        where.append("e.subject_id = ?")
        where_params.append(subject_id)
    if section_id:
        where.append("e.section_id = ?")
        where_params.append(section_id)
    if teacher_id:
        where.append(
            """
            EXISTS (
                SELECT 1 FROM teacher_subjects ts
                WHERE ts.teacher_id = ? AND ts.subject_id = e.subject_id AND ts.section_id = e.section_id
            )
            """
        )
        where_params.append(teacher_id)
    if limit is not None:
        paging_sql = "LIMIT ? OFFSET ?"
        paging_params.extend([limit, offset])

    sql = f"""
        SELECT u.id AS studentId, u.name AS studentName, sp.roll_number AS rollNumber,
               sub.id AS subjectId, sub.code, sub.name AS subjectName,
               sec.id AS sectionId, sec.name AS sectionName, sub.attendance_required AS required,
               COUNT(ar.id) AS totalClasses,
               SUM(CASE WHEN ar.status = 'Present' THEN 1 ELSE 0 END) AS presentClasses,
               SUM(CASE WHEN ar.status = 'Absent' THEN 1 ELSE 0 END) AS absentClasses,
               SUM(CASE WHEN ar.status = 'On Leave' THEN 1 ELSE 0 END) AS leaveClasses
        FROM enrollments e
        JOIN users u ON u.id = e.student_id
        JOIN student_profiles sp ON sp.user_id = u.id
        JOIN subjects sub ON sub.id = e.subject_id
        JOIN sections sec ON sec.id = e.section_id
        LEFT JOIN attendance_records ar ON {" AND ".join(attendance_join)}
        WHERE {" AND ".join(where)}
        GROUP BY e.student_id, e.subject_id, e.section_id
        ORDER BY sec.name, sub.code, sp.roll_number
        {paging_sql}
    """
    rows = conn.execute(sql, join_params + where_params + paging_params).fetchall()
    result = []
    for row in rows:
        item = dict_row(row)
        total = item["totalClasses"] or 0
        present = item["presentClasses"] or 0
        item["percentage"] = round((present / total) * 100, 2) if total else 0
        item["atRisk"] = total > 0 and item["percentage"] < item["required"]
        result.append(item)
    return result


def subject_marks_analytics(conn: sqlite3.Connection, subject_id: int | None = None, section_id: int | None = None, teacher_id: int | None = None) -> list[dict]:
    params: list[object] = []
    where = ["u.status = 'APPROVED'"]
    if subject_id:
        where.append("en.subject_id = ?")
        params.append(subject_id)
    if section_id:
        where.append("en.section_id = ?")
        params.append(section_id)
    if teacher_id:
        where.append(
            """
            EXISTS (
                SELECT 1 FROM teacher_subjects ts
                WHERE ts.teacher_id = ? AND ts.subject_id = en.subject_id AND ts.section_id = en.section_id
            )
            """
        )
        params.append(teacher_id)

    sql = f"""
        SELECT u.id AS studentId, u.name AS studentName, sp.roll_number AS rollNumber,
               sub.id AS subjectId, sub.code, sub.name AS subjectName,
               sec.id AS sectionId, sec.name AS sectionName,
               COUNT(m.id) AS examsGraded,
               SUM(m.marks_obtained) AS marksObtained,
               SUM(e.max_marks) AS maxMarks
        FROM enrollments en
        JOIN users u ON u.id = en.student_id
        JOIN student_profiles sp ON sp.user_id = u.id
        JOIN subjects sub ON sub.id = en.subject_id
        JOIN sections sec ON sec.id = en.section_id
        LEFT JOIN exams e ON e.subject_id = sub.id
        LEFT JOIN marks m ON m.exam_id = e.id AND m.student_id = en.student_id
        WHERE {" AND ".join(where)}
        GROUP BY en.student_id, en.subject_id, en.section_id
        ORDER BY sec.name, sub.code, sp.roll_number
    """
    rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        item = dict_row(row)
        max_marks = item["maxMarks"] or 0
        obtained = item["marksObtained"] or 0
        item["percentage"] = round((obtained / max_marks) * 100, 2) if max_marks else 0
        item["failed"] = max_marks > 0 and item["percentage"] < 40
        result.append(item)
    return result


def admin_dashboard(conn: sqlite3.Connection) -> dict:
    counts = {}
    for role in ("STUDENT", "TEACHER"):
        counts[role.lower() + "s"] = conn.execute("SELECT COUNT(*) AS total FROM users WHERE role = ?", (role,)).fetchone()["total"]
    counts["pending"] = conn.execute("SELECT COUNT(*) AS total FROM users WHERE status = 'PENDING'").fetchone()["total"]
    counts["subjects"] = conn.execute("SELECT COUNT(*) AS total FROM subjects").fetchone()["total"]
    counts["sections"] = conn.execute("SELECT COUNT(*) AS total FROM sections").fetchone()["total"]
    low_attendance = [item for item in attendance_analytics(conn) if item["atRisk"]][:8]
    failed = [item for item in subject_marks_analytics(conn) if item["failed"]][:8]
    toppers = sorted(subject_marks_analytics(conn), key=lambda item: item["percentage"], reverse=True)[:8]
    pending = conn.execute(
        """
        SELECT id, role, name, email, status, created_at AS createdAt
        FROM users
        WHERE status = 'PENDING'
        ORDER BY created_at DESC
        LIMIT 8
        """
    ).fetchall()
    activity = conn.execute(
        """
        SELECT a.id, a.action, a.entity, a.entity_id AS entityId, a.created_at AS createdAt,
               COALESCE(u.name, 'System') AS actor
        FROM activity_logs a
        LEFT JOIN users u ON u.id = a.actor_id
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT 10
        """
    ).fetchall()
    return {
        "stats": counts,
        "pendingUsers": [dict_row(r) for r in pending],
        "lowAttendance": low_attendance,
        "failedStudents": failed,
        "toppers": toppers,
        "recentActivities": [dict_row(r) for r in activity],
    }


def teacher_dashboard(conn: sqlite3.Connection, teacher_id: int) -> dict:
    assignments = teacher_assignments(conn, teacher_id)
    risks = [item for item in attendance_analytics(conn, teacher_id=teacher_id) if item["atRisk"]][:8]
    marks_risks = [item for item in subject_marks_analytics(conn, teacher_id=teacher_id) if item["failed"]][:8]
    recent_attendance = conn.execute(
        """
        SELECT ar.attendance_date AS attendanceDate, sub.code, sub.name AS subjectName,
               sec.name AS sectionName, COUNT(ar.id) AS records
        FROM attendance_records ar
        JOIN subjects sub ON sub.id = ar.subject_id
        JOIN sections sec ON sec.id = ar.section_id
        WHERE ar.teacher_id = ?
        GROUP BY ar.attendance_date, ar.subject_id, ar.section_id
        ORDER BY ar.attendance_date DESC
        LIMIT 8
        """,
        (teacher_id,),
    ).fetchall()
    exams = conn.execute(
        """
        SELECT e.id, e.name, e.exam_type AS examType, e.exam_date AS examDate,
               e.max_marks AS maxMarks, sub.code, sub.name AS subjectName
        FROM exams e
        JOIN subjects sub ON sub.id = e.subject_id
        WHERE EXISTS (
            SELECT 1 FROM teacher_subjects ts
            WHERE ts.teacher_id = ? AND ts.subject_id = e.subject_id
        )
        ORDER BY e.exam_date
        LIMIT 8
        """,
        (teacher_id,),
    ).fetchall()
    return {
        "classes": assignments,
        "atRiskAttendance": risks,
        "atRiskMarks": marks_risks,
        "recentAttendance": [dict_row(r) for r in recent_attendance],
        "upcomingExams": [dict_row(r) for r in exams],
        "notices": latest_notices(conn, "TEACHER"),
    }


def student_dashboard(conn: sqlite3.Connection, student_id: int) -> dict:
    user = full_user(conn, student_id)
    attendance = attendance_summary_for_student(conn, student_id)
    marks = marks_for_student(conn, student_id)
    graded = [m for m in marks if m["marksObtained"] is not None and m["maxMarks"]]
    total_obtained = sum(float(m["marksObtained"]) for m in graded)
    total_max = sum(float(m["maxMarks"]) for m in graded)
    percentage = round((total_obtained / total_max) * 100, 2) if total_max else 0
    latest_marks = sorted(graded, key=lambda m: (m["examDate"] or "", m["examId"]), reverse=True)[:6]
    return {
        "profile": user["profile"],
        "attendance": attendance,
        "latestMarks": latest_marks,
        "overall": {
            "percentage": percentage,
            "grade": grade_for(percentage, 100) if total_max else "N/A",
            "cgpaEstimate": round(percentage / 9.5, 2) if total_max else 0,
        },
        "notices": latest_notices(conn, "STUDENT"),
    }


def list_users(conn: sqlite3.Connection, query: dict[str, list[str]]) -> dict:
    params: list[object] = []
    where = ["1 = 1"]
    role = first(query, "role")
    status = first(query, "status")
    search = first(query, "q")
    page, offset = page_offset(query)
    if role:
        where.append("u.role = ?")
        params.append(role.upper())
    if status:
        where.append("u.status = ?")
        params.append(status.upper())
    if search:
        where.append("(u.name LIKE ? OR u.email LIKE ? OR sp.roll_number LIKE ? OR tp.employee_id LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like, like])
    rows = conn.execute(
        f"""
        SELECT u.id, u.role, u.name, u.email, u.status, u.created_at AS createdAt,
               sp.roll_number AS rollNumber, sp.academic_status AS academicStatus,
               tp.employee_id AS employeeId,
               COALESCE(sd.name, td.name) AS departmentName,
               sec.name AS sectionName
        FROM users u
        LEFT JOIN student_profiles sp ON sp.user_id = u.id
        LEFT JOIN teacher_profiles tp ON tp.user_id = u.id
        LEFT JOIN departments sd ON sd.id = sp.department_id
        LEFT JOIN departments td ON td.id = tp.department_id
        LEFT JOIN sections sec ON sec.id = sp.section_id
        WHERE {" AND ".join(where)}
        ORDER BY u.created_at DESC, u.id DESC
        LIMIT ? OFFSET ?
        """,
        params + [PAGE_SIZE + 1, offset],
    ).fetchall()
    items = [dict_row(r) for r in rows[:PAGE_SIZE]]
    return {"users": items, "page": page, "hasNext": len(rows) > PAGE_SIZE}


def get_structures(conn: sqlite3.Connection) -> dict:
    departments = conn.execute("SELECT id, name, code FROM departments ORDER BY name").fetchall()
    courses = conn.execute(
        """
        SELECT c.id, c.name, c.code, c.duration_semesters AS durationSemesters,
               c.department_id AS departmentId, d.name AS departmentName
        FROM courses c JOIN departments d ON d.id = c.department_id
        ORDER BY c.name
        """
    ).fetchall()
    sections = conn.execute(
        """
        SELECT s.id, s.name, s.semester, s.capacity, s.department_id AS departmentId,
               s.course_id AS courseId, d.name AS departmentName, c.name AS courseName
        FROM sections s
        JOIN departments d ON d.id = s.department_id
        JOIN courses c ON c.id = s.course_id
        ORDER BY d.name, c.name, s.semester, s.name
        """
    ).fetchall()
    subjects = conn.execute(
        """
        SELECT sub.id, sub.code, sub.name, sub.credit, sub.semester,
               sub.attendance_required AS attendanceRequired,
               sub.department_id AS departmentId, sub.course_id AS courseId,
               d.name AS departmentName, c.name AS courseName
        FROM subjects sub
        JOIN departments d ON d.id = sub.department_id
        JOIN courses c ON c.id = sub.course_id
        ORDER BY sub.code
        """
    ).fetchall()
    teachers = conn.execute(
        """
        SELECT u.id, u.name, u.email, tp.employee_id AS employeeId, d.name AS departmentName
        FROM users u
        JOIN teacher_profiles tp ON tp.user_id = u.id
        JOIN departments d ON d.id = tp.department_id
        WHERE u.role = 'TEACHER' AND u.status = 'APPROVED'
        ORDER BY u.name
        """
    ).fetchall()
    assignments = conn.execute(
        """
        SELECT ts.id, ts.teacher_id AS teacherId, u.name AS teacherName,
               ts.subject_id AS subjectId, sub.code, sub.name AS subjectName,
               ts.section_id AS sectionId, sec.name AS sectionName
        FROM teacher_subjects ts
        JOIN users u ON u.id = ts.teacher_id
        JOIN subjects sub ON sub.id = ts.subject_id
        JOIN sections sec ON sec.id = ts.section_id
        ORDER BY u.name, sub.code, sec.name
        """
    ).fetchall()
    return {
        "departments": [dict_row(r) for r in departments],
        "courses": [dict_row(r) for r in courses],
        "sections": [dict_row(r) for r in sections],
        "subjects": [dict_row(r) for r in subjects],
        "teachers": [dict_row(r) for r in teachers],
        "assignments": [dict_row(r) for r in assignments],
    }


def first(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    if not values:
        return default
    return values[0]


def int_field(data: dict, key: str, required: bool = True, default: int | None = None) -> int | None:
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise HttpError(400, f"{key} is required.")
        return default
    try:
        return int(value)
    except ValueError:
        raise HttpError(400, f"{key} must be a number.")


def float_field(data: dict, key: str, required: bool = True, default: float | None = None) -> float | None:
    value = data.get(key)
    if value in (None, ""):
        if required:
            raise HttpError(400, f"{key} is required.")
        return default
    try:
        return float(value)
    except ValueError:
        raise HttpError(400, f"{key} must be a number.")


def page_offset(query: dict[str, list[str]]) -> tuple[int, int]:
    page = max(0, int(first(query, "page", "0") or 0))
    return page, page * PAGE_SIZE


def required_text(data: dict, key: str) -> str:
    value = str(data.get(key, "")).strip()
    if not value:
        raise HttpError(400, f"{key} is required.")
    return value


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HttpError(400, "Invalid date format. Use YYYY-MM-DD.")


def create_csv(headers: list[str], rows: list[list[object]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


class AppHandler(BaseHTTPRequestHandler):
    server_version = "StudentRecordsHTTP/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def do_PUT(self) -> None:
        self.dispatch("PUT")

    def do_DELETE(self) -> None:
        self.dispatch("DELETE")

    def dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                self.handle_api(method, path, query)
            else:
                self.serve_static(path)
        except HttpError as exc:
            self.send_json({"error": exc.message}, exc.status)
        except sqlite3.IntegrityError as exc:
            self.send_json({"error": f"Database constraint failed: {exc}"}, 400)
        except Exception as exc:
            print(f"Unhandled error: {exc}")
            self.send_json({"error": "Unexpected server error."}, 500)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise HttpError(400, "Request body must be valid JSON.")

    def send_json(self, payload: dict | list, status: int = 200, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body: bytes, content_type: str, status: int = 200, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def set_session_cookie(self, token: str) -> str:
        cookie = cookies.SimpleCookie()
        cookie[SESSION_COOKIE] = token
        cookie[SESSION_COOKIE]["httponly"] = True
        cookie[SESSION_COOKIE]["samesite"] = "Lax"
        cookie[SESSION_COOKIE]["path"] = "/"
        cookie[SESSION_COOKIE]["max-age"] = SESSION_DAYS * 24 * 60 * 60
        return cookie.output(header="").strip()

    def clear_session_cookie(self) -> str:
        cookie = cookies.SimpleCookie()
        cookie[SESSION_COOKIE] = ""
        cookie[SESSION_COOKIE]["path"] = "/"
        cookie[SESSION_COOKIE]["max-age"] = 0
        return cookie.output(header="").strip()

    def session_token(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return None
        cookie = cookies.SimpleCookie(raw)
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def current_user(self, conn: sqlite3.Connection) -> dict | None:
        return get_user_by_session(conn, self.session_token())

    def require_user(self, conn: sqlite3.Connection, roles: tuple[str, ...] | None = None) -> dict:
        user = self.current_user(conn)
        if not user:
            raise HttpError(401, "Please log in first.")
        if roles and user["role"] not in roles:
            raise HttpError(403, "You do not have permission for this action.")
        return user

    def serve_static(self, path: str) -> None:
        if path in ("", "/"):
            target = PUBLIC_DIR / "index.html"
        else:
            target = (PUBLIC_DIR / path.lstrip("/")).resolve()
            if not str(target).startswith(str(PUBLIC_DIR.resolve())):
                raise HttpError(403, "Invalid path.")
        if not target.exists() or not target.is_file():
            target = PUBLIC_DIR / "index.html"
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_bytes(target.read_bytes(), content_type)

    def handle_api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        with connect() as conn:
            if method == "GET" and path == "/api/bootstrap":
                self.send_json(public_bootstrap(conn))
                return
            if method == "POST" and path == "/api/register":
                self.register(conn)
                return
            if method == "POST" and path == "/api/login":
                self.login(conn)
                return
            if method == "POST" and path == "/api/logout":
                token = self.session_token()
                if token:
                    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                    conn.commit()
                self.send_json({"ok": True}, extra_headers={"Set-Cookie": self.clear_session_cookie()})
                return
            if method == "GET" and path == "/api/me":
                user = self.require_user(conn)
                self.send_json({"user": full_user(conn, user["id"])})
                return
            if method == "GET" and path == "/api/dashboard":
                user = self.require_user(conn)
                if user["role"] == "ADMIN":
                    self.send_json(admin_dashboard(conn))
                elif user["role"] == "TEACHER":
                    self.send_json(teacher_dashboard(conn, user["id"]))
                else:
                    self.send_json(student_dashboard(conn, user["id"]))
                return
            if method == "GET" and path == "/api/notices":
                user = self.require_user(conn)
                self.send_json({"notices": latest_notices(conn, user["role"])})
                return
            if method == "POST" and path == "/api/change-password":
                user = self.require_user(conn)
                data = self.read_json()
                current_password = required_text(data, "currentPassword")
                new_password = required_text(data, "newPassword")
                confirm_password = required_text(data, "confirmPassword")
                if len(new_password) < 6:
                    raise HttpError(400, "New password must be at least 6 characters.")
                if new_password != confirm_password:
                    raise HttpError(400, "New password and confirm password do not match.")
                row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
                if not row or not verify_password(current_password, row["password_hash"]):
                    raise HttpError(400, "Current password is incorrect.")
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new_password), user["id"]))
                log_activity(conn, user["id"], "Changed password", "user", user["id"])
                conn.commit()
                self.send_json({"message": "Password changed successfully."})
                return
            if method == "POST" and path == "/api/profile/photo":
                user = self.require_user(conn, ("TEACHER", "STUDENT"))
                data = self.read_json()
                photo_data = required_text(data, "photoData")
                if not photo_data.startswith("data:image/"):
                    raise HttpError(400, "Photo must be an image file.")
                if len(photo_data) > 900_000:
                    raise HttpError(400, "Photo is too large. Please choose an image under about 650 KB.")
                conn.execute("UPDATE users SET photo_data = ? WHERE id = ?", (photo_data, user["id"]))
                log_activity(conn, user["id"], "Updated profile photo", "user", user["id"])
                conn.commit()
                self.send_json({"message": "Profile photo updated."})
                return

            if path.startswith("/api/admin/"):
                self.handle_admin(conn, method, path, query)
                return
            if path.startswith("/api/teacher/"):
                self.handle_teacher(conn, method, path, query)
                return
            if path.startswith("/api/student/"):
                self.handle_student(conn, method, path, query)
                return
            raise HttpError(404, "API route not found.")

    def register(self, conn: sqlite3.Connection) -> None:
        data = self.read_json()
        role = required_text(data, "role").upper()
        if role not in ("STUDENT", "TEACHER"):
            raise HttpError(400, "Only students and teachers can self-register.")
        name = required_text(data, "name")
        password = required_text(data, "password")
        if len(password) < 6:
            raise HttpError(400, "Password must be at least 6 characters.")

        if role == "STUDENT":
            identifier = normalized_identifier(required_text(data, "rollNumber"), "Roll number")
        else:
            identifier = normalized_identifier(required_text(data, "employeeId"), "Employee ID")
        email = required_text(data, "email").lower()
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise HttpError(400, "An account already exists for this email.")
        if role == "STUDENT" and conn.execute("SELECT 1 FROM student_profiles WHERE roll_number = ?", (identifier.upper(),)).fetchone():
            raise HttpError(400, "This roll number is already registered.")
        if role == "TEACHER" and conn.execute("SELECT 1 FROM teacher_profiles WHERE employee_id = ?", (identifier.upper(),)).fetchone():
            raise HttpError(400, "This employee ID is already registered.")

        user_id = insert_user(conn, role, name, email, password, "PENDING")
        if role == "STUDENT":
            department_id = int_field(data, "departmentId", False)
            course_id = int_field(data, "courseId", False)
            section_id = int_field(data, "sectionId", False)
            semester = int_field(data, "semester", False, 1)
            conn.execute(
                """
                INSERT INTO student_profiles
                    (user_id, roll_number, department_id, course_id, section_id, semester, phone, guardian_name, academic_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
                """,
                (
                    user_id,
                    identifier.upper(),
                    department_id,
                    course_id,
                    section_id,
                    semester,
                    data.get("phone", ""),
                    data.get("guardianName", ""),
                ),
            )
            if course_id and section_id and semester:
                rows = conn.execute(
                    "SELECT id FROM subjects WHERE course_id = ? AND semester = ?",
                    (course_id, semester),
                ).fetchall()
                for row in rows:
                    conn.execute(
                        "INSERT OR IGNORE INTO enrollments (student_id, subject_id, section_id) VALUES (?, ?, ?)",
                        (user_id, row["id"], section_id),
                    )
        else:
            conn.execute(
                """
                INSERT INTO teacher_profiles (user_id, employee_id, department_id, title, phone)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    identifier.upper(),
                    int_field(data, "departmentId", False),
                    data.get("title", "Assistant Professor"),
                    data.get("phone", ""),
                ),
            )
        log_activity(conn, user_id, f"Registered as {role.lower()}", "user", user_id)
        conn.commit()
        self.send_json({"message": "Registration submitted. Admin approval is required before login.", "userId": user_id}, 201)

    def login(self, conn: sqlite3.Connection) -> None:
        data = self.read_json()
        email = required_text(data, "email").lower()
        password = required_text(data, "password")
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise HttpError(401, "Invalid email or password.")
        if row["status"] != "APPROVED":
            raise HttpError(403, f"Account is {row['status']}. Admin approval is required.")
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now() + timedelta(days=SESSION_DAYS)).replace(microsecond=0).isoformat(sep=" ")
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, row["id"], now_iso(), expires_at),
        )
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso(), row["id"]))
        log_activity(conn, row["id"], "Logged in", "user", row["id"])
        conn.commit()
        self.send_json({"user": full_user(conn, row["id"])}, extra_headers={"Set-Cookie": self.set_session_cookie(token)})

    def handle_admin(self, conn: sqlite3.Connection, method: str, path: str, query: dict[str, list[str]]) -> None:
        admin = self.require_user(conn, ("ADMIN",))
        if method == "GET" and path == "/api/admin/users":
            self.send_json(list_users(conn, query))
            return
        user_detail = re.match(r"^/api/admin/users/(\d+)$", path)
        if user_detail and method == "GET":
            self.send_json({"user": full_user(conn, int(user_detail.group(1)))})
            return
        if user_detail and method == "PUT":
            data = self.read_json()
            target_id = int(user_detail.group(1))
            target = full_user(conn, target_id)
            name = required_text(data, "name")
            email = required_text(data, "email").lower()
            duplicate = conn.execute("SELECT id FROM users WHERE email = ? AND id != ?", (email, target_id)).fetchone()
            if duplicate:
                raise HttpError(400, "Another account already uses this email.")
            conn.execute("UPDATE users SET name = ?, email = ? WHERE id = ?", (name, email, target_id))
            if target["role"] == "STUDENT":
                roll_number = normalized_identifier(required_text(data, "rollNumber"), "Roll number").upper()
                duplicate_roll = conn.execute(
                    "SELECT user_id FROM student_profiles WHERE roll_number = ? AND user_id != ?",
                    (roll_number, target_id),
                ).fetchone()
                if duplicate_roll:
                    raise HttpError(400, "Another student already uses this roll number.")
                conn.execute(
                    """
                    UPDATE student_profiles
                    SET roll_number = ?, department_id = ?, course_id = ?, section_id = ?,
                        semester = ?, phone = ?, guardian_name = ?
                    WHERE user_id = ?
                    """,
                    (
                        roll_number,
                        int_field(data, "departmentId", False),
                        int_field(data, "courseId", False),
                        int_field(data, "sectionId", False),
                        int_field(data, "semester", False, 1),
                        data.get("phone", ""),
                        data.get("guardianName", ""),
                        target_id,
                    ),
                )
            elif target["role"] == "TEACHER":
                employee_id = normalized_identifier(required_text(data, "employeeId"), "Employee ID").upper()
                duplicate_employee = conn.execute(
                    "SELECT user_id FROM teacher_profiles WHERE employee_id = ? AND user_id != ?",
                    (employee_id, target_id),
                ).fetchone()
                if duplicate_employee:
                    raise HttpError(400, "Another teacher already uses this employee ID.")
                conn.execute(
                    """
                    UPDATE teacher_profiles
                    SET employee_id = ?, department_id = ?, title = ?, phone = ?
                    WHERE user_id = ?
                    """,
                    (
                        employee_id,
                        int_field(data, "departmentId", False),
                        data.get("title", "Assistant Professor"),
                        data.get("phone", ""),
                        target_id,
                    ),
                )
            log_activity(conn, admin["id"], "Updated user details", "user", target_id)
            conn.commit()
            self.send_json({"message": "User details updated."})
            return
        if method == "GET" and path == "/api/admin/pending-users":
            self.send_json(list_users(conn, {"status": ["PENDING"]}))
            return
        user_action = re.match(r"^/api/admin/users/(\d+)/(approve|reject|reset-password)$", path)
        if user_action and method == "POST":
            target_id = int(user_action.group(1))
            action = user_action.group(2)
            if action == "approve":
                conn.execute("UPDATE users SET status = 'APPROVED' WHERE id = ?", (target_id,))
                log_activity(conn, admin["id"], "Approved user account", "user", target_id)
                conn.commit()
                self.send_json({"message": "User approved."})
                return
            if action == "reject":
                conn.execute("UPDATE users SET status = 'REJECTED' WHERE id = ?", (target_id,))
                log_activity(conn, admin["id"], "Rejected user account", "user", target_id)
                conn.commit()
                self.send_json({"message": "User rejected."})
                return
            temp_password = "College@123"
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(temp_password), target_id))
            log_activity(conn, admin["id"], "Reset user password", "user", target_id)
            conn.commit()
            self.send_json({"message": "Password reset.", "temporaryPassword": temp_password})
            return
        status_action = re.match(r"^/api/admin/students/(\d+)/status$", path)
        if status_action and method == "POST":
            data = self.read_json()
            academic_status = required_text(data, "academicStatus").upper()
            if academic_status not in ("ACTIVE", "INACTIVE", "DETAINED", "NOT_ELIGIBLE"):
                raise HttpError(400, "Invalid academic status.")
            student_id = int(status_action.group(1))
            conn.execute("UPDATE student_profiles SET academic_status = ? WHERE user_id = ?", (academic_status, student_id))
            log_activity(conn, admin["id"], f"Updated student academic status to {academic_status}", "student", student_id)
            conn.commit()
            self.send_json({"message": "Student status updated."})
            return
        if method == "GET" and path == "/api/admin/structures":
            self.send_json(get_structures(conn))
            return
        if method == "POST" and path == "/api/admin/departments":
            data = self.read_json()
            cur = conn.execute(
                "INSERT INTO departments (name, code) VALUES (?, ?)",
                (required_text(data, "name"), required_text(data, "code").upper()),
            )
            log_activity(conn, admin["id"], "Created department", "department", cur.lastrowid)
            conn.commit()
            self.send_json({"message": "Department created.", "id": cur.lastrowid}, 201)
            return
        if method == "POST" and path == "/api/admin/courses":
            data = self.read_json()
            cur = conn.execute(
                "INSERT INTO courses (department_id, name, code, duration_semesters) VALUES (?, ?, ?, ?)",
                (
                    int_field(data, "departmentId"),
                    required_text(data, "name"),
                    required_text(data, "code").upper(),
                    int_field(data, "durationSemesters", False, 8),
                ),
            )
            log_activity(conn, admin["id"], "Created course", "course", cur.lastrowid)
            conn.commit()
            self.send_json({"message": "Course created.", "id": cur.lastrowid}, 201)
            return
        if method == "POST" and path == "/api/admin/sections":
            data = self.read_json()
            cur = conn.execute(
                """
                INSERT INTO sections (department_id, course_id, semester, name, capacity)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int_field(data, "departmentId"),
                    int_field(data, "courseId"),
                    int_field(data, "semester"),
                    required_text(data, "name"),
                    int_field(data, "capacity", False, 60),
                ),
            )
            log_activity(conn, admin["id"], "Created section", "section", cur.lastrowid)
            conn.commit()
            self.send_json({"message": "Section created.", "id": cur.lastrowid}, 201)
            return
        if method == "POST" and path == "/api/admin/subjects":
            data = self.read_json()
            cur = conn.execute(
                """
                INSERT INTO subjects (department_id, course_id, semester, code, name, credit, attendance_required)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int_field(data, "departmentId"),
                    int_field(data, "courseId"),
                    int_field(data, "semester"),
                    required_text(data, "code").upper(),
                    required_text(data, "name"),
                    int_field(data, "credit", False, 4),
                    float_field(data, "attendanceRequired", False, 75),
                ),
            )
            log_activity(conn, admin["id"], "Created subject", "subject", cur.lastrowid)
            conn.commit()
            self.send_json({"message": "Subject created.", "id": cur.lastrowid}, 201)
            return
        if method == "POST" and path == "/api/admin/assignments":
            data = self.read_json()
            cur = conn.execute(
                "INSERT INTO teacher_subjects (teacher_id, subject_id, section_id) VALUES (?, ?, ?)",
                (int_field(data, "teacherId"), int_field(data, "subjectId"), int_field(data, "sectionId")),
            )
            log_activity(conn, admin["id"], "Assigned teacher to subject", "assignment", cur.lastrowid)
            conn.commit()
            self.send_json({"message": "Teacher assignment created.", "id": cur.lastrowid}, 201)
            return
        if method == "POST" and path == "/api/admin/notices":
            data = self.read_json()
            audience = required_text(data, "audience").upper()
            if audience not in ("ALL", "STUDENT", "TEACHER"):
                raise HttpError(400, "Invalid audience.")
            cur = conn.execute(
                "INSERT INTO notices (title, message, audience, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (required_text(data, "title"), required_text(data, "message"), audience, admin["id"], now_iso()),
            )
            log_activity(conn, admin["id"], "Published notice", "notice", cur.lastrowid)
            conn.commit()
            self.send_json({"message": "Notice published.", "id": cur.lastrowid}, 201)
            return
        if method == "GET" and path == "/api/admin/reports/attendance":
            self.send_json(self.attendance_report(conn, query))
            return
        if method == "GET" and path == "/api/admin/reports/marks":
            self.send_json(self.marks_report(conn, query))
            return
        if method == "GET" and path == "/api/admin/reports/attendance.csv":
            rows = self.attendance_report(conn, query)["rows"]
            csv_body = create_csv(
                ["Roll", "Student", "Section", "Subject", "Total", "Present", "Absent", "Leave", "Percentage", "Required"],
                [
                    [
                        r["rollNumber"],
                        r["studentName"],
                        r["sectionName"],
                        f"{r['code']} {r['subjectName']}",
                        r["totalClasses"],
                        r["presentClasses"],
                        r["absentClasses"],
                        r["leaveClasses"],
                        r["percentage"],
                        r["required"],
                    ]
                    for r in rows
                ],
            )
            self.send_bytes(
                csv_body,
                "text/csv; charset=utf-8",
                extra_headers={"Content-Disposition": 'attachment; filename="attendance-report.csv"'},
            )
            return
        if method == "GET" and path == "/api/admin/reports/marks.csv":
            rows = self.marks_report(conn, query)["rows"]
            csv_body = create_csv(
                ["Roll", "Student", "Section", "Subject", "Exam", "Marks", "Max", "Grade", "Percentage"],
                [
                    [
                        r["rollNumber"],
                        r["studentName"],
                        r["sectionName"],
                        f"{r['code']} {r['subjectName']}",
                        r["examName"],
                        r["marksObtained"],
                        r["maxMarks"],
                        r["grade"],
                        r["percentage"],
                    ]
                    for r in rows
                ],
            )
            self.send_bytes(
                csv_body,
                "text/csv; charset=utf-8",
                extra_headers={"Content-Disposition": 'attachment; filename="marks-report.csv"'},
            )
            return
        raise HttpError(404, "Admin route not found.")

    def attendance_report(self, conn: sqlite3.Connection, query: dict[str, list[str]]) -> list[dict]:
        subject_id = int(first(query, "subjectId", "0") or 0) or None
        section_id = int(first(query, "sectionId", "0") or 0) or None
        page, offset = page_offset(query)
        date_from = first(query, "from")
        date_to = first(query, "to")
        if date_from:
            parse_date(date_from)
        if date_to:
            parse_date(date_to)
        rows = attendance_analytics(
            conn,
            subject_id=subject_id,
            section_id=section_id,
            date_from=date_from or None,
            date_to=date_to or None,
            limit=PAGE_SIZE + 1,
            offset=offset,
        )
        return {"rows": rows[:PAGE_SIZE], "page": page, "hasNext": len(rows) > PAGE_SIZE}

    def marks_report(self, conn: sqlite3.Connection, query: dict[str, list[str]]) -> dict:
        subject_id = int(first(query, "subjectId", "0") or 0) or None
        section_id = int(first(query, "sectionId", "0") or 0) or None
        exam_id = int(first(query, "examId", "0") or 0) or None
        page, offset = page_offset(query)
        params: list[object] = []
        where = ["u.status = 'APPROVED'"]
        if subject_id:
            where.append("sub.id = ?")
            params.append(subject_id)
        if section_id:
            where.append("sec.id = ?")
            params.append(section_id)
        if exam_id:
            where.append("e.id = ?")
            params.append(exam_id)
        rows = conn.execute(
            f"""
            SELECT u.id AS studentId, u.name AS studentName, sp.roll_number AS rollNumber,
                   sec.name AS sectionName, sub.code, sub.name AS subjectName,
                   e.id AS examId, e.name AS examName, e.max_marks AS maxMarks,
                   m.marks_obtained AS marksObtained, COALESCE(m.grade, 'N/A') AS grade
            FROM enrollments en
            JOIN users u ON u.id = en.student_id
            JOIN student_profiles sp ON sp.user_id = u.id
            JOIN sections sec ON sec.id = en.section_id
            JOIN subjects sub ON sub.id = en.subject_id
            JOIN exams e ON e.subject_id = sub.id
            LEFT JOIN marks m ON m.exam_id = e.id AND m.student_id = u.id
            WHERE {" AND ".join(where)}
            ORDER BY sec.name, sub.code, e.exam_date, sp.roll_number
            LIMIT ? OFFSET ?
            """,
            params + [PAGE_SIZE + 1, offset],
        ).fetchall()
        result = []
        for row in rows[:PAGE_SIZE]:
            item = dict_row(row)
            marks = item["marksObtained"]
            max_marks = item["maxMarks"] or 0
            item["percentage"] = round((float(marks) / float(max_marks)) * 100, 2) if marks is not None and max_marks else 0
            result.append(item)
        return {"rows": result, "page": page, "hasNext": len(rows) > PAGE_SIZE}

    def handle_teacher(self, conn: sqlite3.Connection, method: str, path: str, query: dict[str, list[str]]) -> None:
        teacher = self.require_user(conn, ("TEACHER",))
        if method == "GET" and path == "/api/teacher/classes":
            self.send_json({"classes": teacher_assignments(conn, teacher["id"])})
            return
        if method == "GET" and path == "/api/teacher/attendance":
            subject_id = int(first(query, "subjectId"))
            section_id = int(first(query, "sectionId"))
            attendance_date = first(query, "date", today_iso())
            if parse_date(attendance_date) > date.today():
                raise HttpError(400, "Attendance cannot be recorded for a future date.")
            if not teacher_can_access(conn, teacher["id"], subject_id, section_id):
                raise HttpError(403, "This class is not assigned to you.")
            roster = class_roster(conn, subject_id, section_id)
            existing = conn.execute(
                """
                SELECT student_id AS studentId, status, reason, correction_reason AS correctionReason
                FROM attendance_records
                WHERE subject_id = ? AND section_id = ? AND attendance_date = ?
                """,
                (subject_id, section_id, attendance_date),
            ).fetchall()
            by_student = {r["studentId"]: dict_row(r) for r in existing}
            for student in roster:
                record = by_student.get(student["studentId"], {})
                student["status"] = record.get("status", "Present")
                student["reason"] = record.get("reason", "")
                student["correctionReason"] = record.get("correctionReason", "")
            self.send_json({"students": roster, "editWindowDays": ATTENDANCE_EDIT_DAYS})
            return
        if method == "POST" and path == "/api/teacher/attendance":
            data = self.read_json()
            subject_id = int_field(data, "subjectId")
            section_id = int_field(data, "sectionId")
            attendance_date = required_text(data, "date")
            correction_reason = str(data.get("correctionReason", "")).strip()
            if not teacher_can_access(conn, teacher["id"], subject_id, section_id):
                raise HttpError(403, "This class is not assigned to you.")
            target_date = parse_date(attendance_date)
            if target_date > date.today():
                raise HttpError(400, "Attendance cannot be recorded for a future date.")
            if target_date < date.today() - timedelta(days=ATTENDANCE_EDIT_DAYS):
                raise HttpError(400, f"Attendance can only be edited within {ATTENDANCE_EDIT_DAYS} days.")
            records = data.get("records", [])
            if not records:
                raise HttpError(400, "At least one attendance record is required.")
            existing_count = conn.execute(
                "SELECT COUNT(*) AS total FROM attendance_records WHERE subject_id = ? AND section_id = ? AND attendance_date = ?",
                (subject_id, section_id, attendance_date),
            ).fetchone()["total"]
            allowed_students = {student["studentId"] for student in class_roster(conn, subject_id, section_id)}
            for record in records:
                student_id = int_field(record, "studentId")
                if student_id not in allowed_students:
                    raise HttpError(403, "One or more students are not enrolled in this class.")
                status = required_text(record, "status")
                if status not in ("Present", "Absent", "On Leave"):
                    raise HttpError(400, "Invalid attendance status.")
                conn.execute(
                    """
                    INSERT INTO attendance_records
                        (student_id, subject_id, section_id, teacher_id, attendance_date, status, reason,
                         correction_reason, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(student_id, subject_id, attendance_date) DO UPDATE SET
                        status = excluded.status,
                        reason = excluded.reason,
                        correction_reason = excluded.correction_reason,
                        teacher_id = excluded.teacher_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        student_id,
                        subject_id,
                        section_id,
                        teacher["id"],
                        attendance_date,
                        status,
                        record.get("reason", ""),
                        correction_reason,
                        now_iso(),
                        now_iso(),
                    ),
                )
            log_activity(conn, teacher["id"], "Submitted attendance", "attendance", subject_id)
            conn.commit()
            self.send_json({"message": "Attendance saved."})
            return
        if method == "GET" and path == "/api/teacher/exams":
            subject_id = int(first(query, "subjectId"))
            section_id = int(first(query, "sectionId", "0") or 0)
            if section_id and not teacher_can_access(conn, teacher["id"], subject_id, section_id):
                raise HttpError(403, "This subject is not assigned to you.")
            rows = conn.execute(
                """
                SELECT id, name, exam_type AS examType, max_marks AS maxMarks, weight, exam_date AS examDate
                FROM exams
                WHERE subject_id = ?
                ORDER BY exam_date, id
                """,
                (subject_id,),
            ).fetchall()
            self.send_json({"exams": [dict_row(r) for r in rows]})
            return
        if method == "POST" and path == "/api/teacher/exams":
            data = self.read_json()
            subject_id = int_field(data, "subjectId")
            section_id = int_field(data, "sectionId")
            if not teacher_can_access(conn, teacher["id"], subject_id, section_id):
                raise HttpError(403, "This subject is not assigned to you.")
            cur = conn.execute(
                """
                INSERT INTO exams (subject_id, name, exam_type, max_marks, weight, exam_date)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    subject_id,
                    required_text(data, "name"),
                    required_text(data, "examType").upper(),
                    float_field(data, "maxMarks"),
                    float_field(data, "weight", False, 1),
                    data.get("examDate", today_iso()),
                ),
            )
            log_activity(conn, teacher["id"], "Created exam component", "exam", cur.lastrowid)
            conn.commit()
            self.send_json({"message": "Exam created.", "id": cur.lastrowid}, 201)
            return
        if method == "GET" and path == "/api/teacher/marks":
            subject_id = int(first(query, "subjectId"))
            section_id = int(first(query, "sectionId"))
            exam_id = int(first(query, "examId"))
            if not teacher_can_access(conn, teacher["id"], subject_id, section_id):
                raise HttpError(403, "This class is not assigned to you.")
            exam = conn.execute("SELECT * FROM exams WHERE id = ? AND subject_id = ?", (exam_id, subject_id)).fetchone()
            if not exam:
                raise HttpError(404, "Exam not found.")
            roster = class_roster(conn, subject_id, section_id)
            existing = conn.execute(
                "SELECT student_id AS studentId, marks_obtained AS marksObtained, grade, remarks FROM marks WHERE exam_id = ?",
                (exam_id,),
            ).fetchall()
            by_student = {r["studentId"]: dict_row(r) for r in existing}
            for student in roster:
                record = by_student.get(student["studentId"], {})
                student["marksObtained"] = record.get("marksObtained")
                student["grade"] = record.get("grade", "")
                student["remarks"] = record.get("remarks", "")
            self.send_json({"students": roster, "exam": dict_row(exam)})
            return
        if method == "POST" and path == "/api/teacher/marks":
            data = self.read_json()
            subject_id = int_field(data, "subjectId")
            section_id = int_field(data, "sectionId")
            exam_id = int_field(data, "examId")
            if not teacher_can_access(conn, teacher["id"], subject_id, section_id):
                raise HttpError(403, "This class is not assigned to you.")
            exam = conn.execute("SELECT * FROM exams WHERE id = ? AND subject_id = ?", (exam_id, subject_id)).fetchone()
            if not exam:
                raise HttpError(404, "Exam not found.")
            allowed_students = {student["studentId"] for student in class_roster(conn, subject_id, section_id)}
            for record in data.get("records", []):
                student_id = int_field(record, "studentId")
                if student_id not in allowed_students:
                    raise HttpError(403, "One or more students are not enrolled in this class.")
                raw_score = record.get("marksObtained")
                if raw_score in (None, ""):
                    continue
                score = float(raw_score)
                if score < 0 or score > float(exam["max_marks"]):
                    raise HttpError(400, f"Marks must be between 0 and {exam['max_marks']}.")
                conn.execute(
                    """
                    INSERT INTO marks
                        (student_id, subject_id, exam_id, teacher_id, marks_obtained, grade, remarks, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(student_id, exam_id) DO UPDATE SET
                        marks_obtained = excluded.marks_obtained,
                        grade = excluded.grade,
                        remarks = excluded.remarks,
                        teacher_id = excluded.teacher_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        student_id,
                        subject_id,
                        exam_id,
                        teacher["id"],
                        score,
                        grade_for(score, float(exam["max_marks"])),
                        record.get("remarks", ""),
                        now_iso(),
                        now_iso(),
                    ),
                )
            log_activity(conn, teacher["id"], "Submitted marks", "marks", exam_id)
            conn.commit()
            self.send_json({"message": "Marks saved."})
            return
        if method == "GET" and path == "/api/teacher/analytics":
            subject_id = int(first(query, "subjectId"))
            section_id = int(first(query, "sectionId"))
            if not teacher_can_access(conn, teacher["id"], subject_id, section_id):
                raise HttpError(403, "This class is not assigned to you.")
            self.send_json(
                {
                    "attendance": attendance_analytics(conn, subject_id=subject_id, section_id=section_id, teacher_id=teacher["id"]),
                    "marks": subject_marks_analytics(conn, subject_id=subject_id, section_id=section_id, teacher_id=teacher["id"]),
                }
            )
            return
        if method == "GET" and path == "/api/teacher/export/attendance.csv":
            subject_id = int(first(query, "subjectId"))
            section_id = int(first(query, "sectionId"))
            if not teacher_can_access(conn, teacher["id"], subject_id, section_id):
                raise HttpError(403, "This class is not assigned to you.")
            rows = attendance_analytics(conn, subject_id=subject_id, section_id=section_id, teacher_id=teacher["id"])
            csv_body = create_csv(
                ["Roll", "Student", "Section", "Subject", "Total", "Present", "Absent", "Leave", "Percentage"],
                [
                    [
                        r["rollNumber"],
                        r["studentName"],
                        r["sectionName"],
                        f"{r['code']} {r['subjectName']}",
                        r["totalClasses"],
                        r["presentClasses"],
                        r["absentClasses"],
                        r["leaveClasses"],
                        r["percentage"],
                    ]
                    for r in rows
                ],
            )
            self.send_bytes(
                csv_body,
                "text/csv; charset=utf-8",
                extra_headers={"Content-Disposition": 'attachment; filename="teacher-attendance.csv"'},
            )
            return
        raise HttpError(404, "Teacher route not found.")

    def handle_student(self, conn: sqlite3.Connection, method: str, path: str, query: dict[str, list[str]]) -> None:
        student = self.require_user(conn, ("STUDENT",))
        if method == "GET" and path == "/api/student/attendance":
            page, offset = page_offset(query)
            summary = attendance_summary_for_student(conn, student["id"])
            rows = conn.execute(
                """
                SELECT ar.id AS attendanceId, ar.attendance_date AS attendanceDate, ar.status,
                       ar.reason, ar.student_absence_reason AS studentAbsenceReason,
                       ar.absence_reason_updated_at AS absenceReasonUpdatedAt,
                       sub.code, sub.name AS subjectName
                FROM attendance_records ar
                JOIN subjects sub ON sub.id = ar.subject_id
                WHERE ar.student_id = ?
                ORDER BY ar.attendance_date DESC, sub.code
                LIMIT ? OFFSET ?
                """,
                (student["id"], PAGE_SIZE + 1, offset),
            ).fetchall()
            self.send_json(
                {
                    "summary": summary,
                    "records": [dict_row(r) for r in rows[:PAGE_SIZE]],
                    "page": page,
                    "hasNext": len(rows) > PAGE_SIZE,
                }
            )
            return
        reason_action = re.match(r"^/api/student/attendance/(\d+)/reason$", path)
        if reason_action and method == "POST":
            attendance_id = int(reason_action.group(1))
            data = self.read_json()
            reason = required_text(data, "reason")
            if len(reason) > 500:
                raise HttpError(400, "Reason must be 500 characters or fewer.")
            record = conn.execute(
                "SELECT id, status FROM attendance_records WHERE id = ? AND student_id = ?",
                (attendance_id, student["id"]),
            ).fetchone()
            if not record:
                raise HttpError(404, "Attendance record not found.")
            if record["status"] != "Absent":
                raise HttpError(400, "Absence reason can only be submitted for Absent records.")
            conn.execute(
                """
                UPDATE attendance_records
                SET student_absence_reason = ?, absence_reason_updated_at = ?, updated_at = ?
                WHERE id = ? AND student_id = ?
                """,
                (reason, now_iso(), now_iso(), attendance_id, student["id"]),
            )
            log_activity(conn, student["id"], "Submitted absence reason", "attendance", attendance_id)
            conn.commit()
            self.send_json({"message": "Absence reason saved."})
            return
        if method == "GET" and path == "/api/student/marks":
            page, offset = page_offset(query)
            marks = marks_for_student(conn, student["id"], PAGE_SIZE + 1, offset)
            self.send_json({"marks": marks[:PAGE_SIZE], "page": page, "hasNext": len(marks) > PAGE_SIZE})
            return
        raise HttpError(404, "Student route not found.")


def main() -> None:
    init_database()
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Student Academic Record & Attendance Management System")
    print(f"Running at http://{HOST}:{PORT}")
    print("Initial admin login: admin@college.local/admin123")
    server.serve_forever()


if __name__ == "__main__":
    main()
