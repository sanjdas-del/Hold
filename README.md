# Hold (OfficeHours)

Campus office-hours board. TAs post twenty-minute slots. Students hold one, read any private note the TA left, cancel if they cannot make it, or pass the slot to a classmate.

Flask, Jinja, and SQLite.

## Features

- Register with a `.edu` email, sign in, sign out
- Public board of upcoming slots
- Students hold an open slot
- Booking detail, including a private TA note when there is one
- Cancel a hold or transfer it by email
- TAs post new slots and see who booked
- One-time device link on "My bookings" that signs in another browser without copying the session cookie

## Run locally

Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open [http://localhost:3000](http://localhost:3000). The database is created and seeded on first request at `data/officehours.db`.

```bash
python seed.py
python -m unittest discover -s tests -t .
```

## Run with Docker

```bash
docker compose up --build
```

The app listens on port 3000. Data lives in the `hold-data` volume.

Set `SECRET_KEY` in the environment before you host this for anyone else. That key signs the short-lived flash and form-token cookie. It is not the login session.

## Demo accounts

| Email | Password | Role |
|---|---|---|
| `lea@campus.edu` | `campus123` | student (already holds two slots) |
| `nico@campus.edu` | `campus123` | student |
| `ravi@campus.edu` | `ta-office` | TA |

New registrations are student accounts.

## Project layout

```
app.py                 Routes, sessions, booking flow
db.py                  SQLite schema
seed.py                First-run demo data
templates/             Jinja pages
static/css/style.css
SECURITY.md            Architecture sketch and the security writeup
```

## Assignment notes

Host this somewhere your classmates and instructor can reach. Walk the running app until you can explain:

- how a login becomes a cookie the browser sends back
- the difference between the public board and a booking detail
- which actions change server state (book, cancel, transfer, logout)
- what the device-handoff link on My bookings is doing

The defect in the original board, how to trigger it, and the patch are written up in [SECURITY.md](SECURITY.md).
