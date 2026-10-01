from werkzeug.security import generate_password_hash

from db import get_db, init_db


def seed():
    init_db()
    conn = get_db()
    existing = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if existing:
        conn.close()
        return {"seeded": False, "users": existing}

    pw = generate_password_hash("campus123")
    ta_pw = generate_password_hash("ta-office")

    conn.execute(
        "INSERT INTO users (email, password_hash, display_name, role) VALUES (?, ?, ?, ?)",
        ("ravi@campus.edu", ta_pw, "Ravi Desai", "ta"),
    )
    conn.execute(
        "INSERT INTO users (email, password_hash, display_name, role) VALUES (?, ?, ?, ?)",
        ("lea@campus.edu", pw, "Lea Okonkwo", "student"),
    )
    conn.execute(
        "INSERT INTO users (email, password_hash, display_name, role) VALUES (?, ?, ?, ?)",
        ("nico@campus.edu", pw, "Nico Park", "student"),
    )
    ravi = conn.execute("SELECT id FROM users WHERE email = 'ravi@campus.edu'").fetchone()["id"]
    lea = conn.execute("SELECT id FROM users WHERE email = 'lea@campus.edu'").fetchone()["id"]

    slots = [
        (ravi, "2026-09-17 13:00", "Evans 204", "CPEG 470 project questions"),
        (ravi, "2026-09-17 13:20", "Evans 204", "CPEG 470 project questions"),
        (ravi, "2026-09-17 13:40", "Evans 204", "Exam review — limited seats"),
        (ravi, "2026-09-18 10:00", "Zoom (link sent after booking)", "Remote office hours"),
        (ravi, "2026-09-18 10:20", "Zoom (link sent after booking)", "Remote office hours"),
    ]
    slot_ids = []
    for row in slots:
        cur = conn.execute(
            "INSERT INTO slots (ta_id, starts_at, location, topic) VALUES (?, ?, ?, ?)",
            row,
        )
        slot_ids.append(cur.lastrowid)

    conn.execute(
        """
        INSERT INTO bookings (slot_id, student_id, private_note)
        VALUES (?, ?, ?)
        """,
        (
            slot_ids[2],
            lea,
            "Makeup midterm window: bring ID to Evans 204. Release code HOLD-2291. Do not forward this note.",
        ),
    )
    conn.execute(
        """
        INSERT INTO bookings (slot_id, student_id, private_note)
        VALUES (?, ?, ?)
        """,
        (slot_ids[0], lea, None),
    )

    conn.commit()
    conn.close()
    return {"seeded": True, "users": 3}


if __name__ == "__main__":
    result = seed()
    print("Seeded." if result["seeded"] else "Already seeded.")
