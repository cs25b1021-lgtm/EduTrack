# Student Academic Record & Attendance Management System

This project is a complete college web app for managing students, teachers, subjects, attendance, marks, approvals, dashboards, and reports.

## Refined Requirements

- Roles are `ADMIN`, `TEACHER`, and `STUDENT`.
- New students and teachers register into `PENDING` status. Admin approves or rejects them once.
- Admin manages academic structures, users, notices, reports, and student academic status.
- Teachers only work with subjects and sections assigned to them.
- Students only see their own profile, attendance, marks, grades, and notices.
- Attendance supports `Present`, `Absent`, and `On Leave`, with a 7-day correction window.
- Marks are entered per exam component and grades are calculated automatically.
- Reports can be viewed in-app and exported as CSV.

## Tech Stack

- Python 3 standard library HTTP server
- SQLite database
- Vanilla HTML, CSS, and JavaScript
- No external packages required

The architecture is intentionally beginner-friendly:

- `app.py`: data models, database setup, business rules, role-based APIs, sessions
- `public/index.html`: application shell
- `public/styles.css`: responsive UI styling
- `public/app.js`: role dashboards and frontend workflows
- `data/student_records_v2.sqlite3`: generated automatically on first run

## Run

```powershell
python app.py
```

Open:

```text
http://127.0.0.1:8000
```

## Initial Accounts

| Role | Email | Password |
| --- | --- | --- |
| Admin | `admin@college.local` | `admin123` |

The app starts clean with only the admin account. Create departments, courses, sections, subjects, then register and approve teachers/students.

## Core Workflows To Try

1. Register a new student or teacher.
2. Log in as Admin and approve the pending account.
3. Log in as Teacher and record attendance for an assigned class.
4. Log in as Student and verify the attendance percentage changed.
5. Log in as Teacher and enter marks for an exam.
6. Log in as Student and view updated marks and grade.
7. Log in as Admin and export attendance or marks reports as CSV.

## Notes

This app uses server-side role checks, HTTP-only sessions, PBKDF2 password hashing, and a persistent SQLite database. It is suitable for an academic project. For production deployment, add HTTPS, CSRF protection, audit retention policies, stronger password reset flow, and email/OTP verification.
