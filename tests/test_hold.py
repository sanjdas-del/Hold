import os
import re
import tempfile
import unittest

from app import app
from db import get_db


class HoldTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["DATABASE_PATH"] = os.path.join(self.tmp.name, "officehours.db")
        app.config["TESTING"] = True
        self.client = app.test_client()

    def csrf(self, path="/login"):
        html = self.client.get(path).get_data(as_text=True)
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        self.assertIsNotNone(match, html)
        return match.group(1)

    def login(self, email, password, client=None):
        client = client or self.client
        html = client.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        return client.post(
            "/login",
            data={"email": email, "password": password, "csrf_token": token},
            follow_redirects=True,
        )

    def session_token(self, client=None):
        client = client or self.client
        cookie = client.get_cookie("hold_session")
        self.assertIsNotNone(cookie)
        return cookie.value

    def test_public_board_hides_private_note(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Exam review", html)
        self.assertIn("held", html)
        self.assertNotIn("HOLD-2291", html)
        self.assertNotIn("lea@campus.edu", html)
        self.assertNotIn("private_note", html)

    def test_login_sets_httponly_cookie_and_shows_note_to_holder(self):
        form = self.client.get("/login").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', form).group(1)
        response = self.client.post(
            "/login",
            data={"email": "lea@campus.edu", "password": "campus123", "csrf_token": csrf},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        set_cookie = " ".join(response.headers.getlist("Set-Cookie"))
        self.assertIn("hold_session=", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Lax", set_cookie)

        html = self.client.get("/me").get_data(as_text=True)
        self.assertIn("My bookings", html)
        self.assertIn("Exam review", html)
        token = self.session_token()
        self.assertNotIn(token, html)
        self.assertNotIn("?sid=", html)

        detail = None
        for href in re.findall(r'href="(/bookings/\d+)"', html):
            page = self.client.get(href).get_data(as_text=True)
            if "HOLD-2291" in page:
                detail = page
                break
        self.assertIsNotNone(detail)
        self.assertIn("Do not forward this note", detail)

    def test_sid_query_does_not_authenticate(self):
        self.login("lea@campus.edu", "campus123")
        token = self.session_token()
        stranger = app.test_client()
        page = stranger.get(f"/me?sid={token}", follow_redirects=True).get_data(as_text=True)
        self.assertIn("Sign in", page)
        self.assertNotIn("HOLD-2291", page)
        self.assertNotIn("Exam review", page)
        self.assertIsNone(stranger.get_cookie("hold_session"))

    def test_other_student_cannot_read_note_or_cancel(self):
        self.login("nico@campus.edu", "campus123")
        page = self.client.get("/bookings/1").get_data(as_text=True)
        self.assertIn("Not allowed", page)
        self.assertNotIn("HOLD-2291", page)

        token = self.csrf("/me")
        cancelled = self.client.post(
            "/bookings/1/cancel",
            data={"csrf_token": token},
            follow_redirects=True,
        )
        self.assertEqual(cancelled.status_code, 403)
        self.assertNotIn("HOLD-2291", cancelled.get_data(as_text=True))

        owner = app.test_client()
        self.login("lea@campus.edu", "campus123", client=owner)
        still_there = owner.get("/bookings/1").get_data(as_text=True)
        self.assertIn("HOLD-2291", still_there)

    def test_get_cancel_and_transfer_do_not_change_state(self):
        self.login("lea@campus.edu", "campus123")
        gone = self.client.get("/bookings/1/cancel?x=1", follow_redirects=True)
        self.assertIn("confirmed from the booking page", gone.get_data(as_text=True))
        self.assertIn("HOLD-2291", gone.get_data(as_text=True))

        moved = self.client.get(
            "/bookings/1/transfer?email=nico@campus.edu",
            follow_redirects=True,
        )
        body = moved.get_data(as_text=True)
        self.assertIn("transfer form", body)
        self.assertIn("HOLD-2291", body)

        self.client.post("/bookings/1/cancel", data={}, follow_redirects=True)
        self.assertIn("HOLD-2291", self.client.get("/bookings/1").get_data(as_text=True))

    def test_holder_can_cancel_with_form(self):
        self.login("lea@campus.edu", "campus123")
        token = self.csrf("/bookings/2")
        page = self.client.post(
            "/bookings/2/cancel",
            data={"csrf_token": token},
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("Booking cancelled", page)
        self.assertNotIn("2026-09-17 13:00", page)
        board = self.client.get("/").get_data(as_text=True)
        self.assertEqual(board.lower().count(">held<"), 1)
        self.assertIn("Hold this", board)

    def test_holder_can_transfer_and_recipient_reads_note(self):
        self.login("lea@campus.edu", "campus123")
        token = self.csrf("/bookings/1")
        page = self.client.post(
            "/bookings/1/transfer",
            data={"csrf_token": token, "email": "nico@campus.edu"},
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("Transferred", page)
        self.assertNotIn("Exam review", page)

        nico = app.test_client()
        self.login("nico@campus.edu", "campus123", client=nico)
        detail = nico.get("/bookings/1").get_data(as_text=True)
        self.assertIn("HOLD-2291", detail)
        self.assertIn("nico@campus.edu", detail)

    def test_device_handoff_is_one_time_and_not_the_session_cookie(self):
        self.login("lea@campus.edu", "campus123")
        token = self.session_token()
        csrf = self.csrf("/me")
        page = self.client.post("/me", data={"csrf_token": csrf}).get_data(as_text=True)
        match = re.search(r'value="(https?://[^"]+/handoff/([^"]+))"', page)
        self.assertIsNotNone(match, page)
        code = match.group(2)
        self.assertNotEqual(code, token)
        self.assertNotIn(token, page)
        self.assertNotIn("sid=", page)

        other = app.test_client()
        landed = other.get(f"/handoff/{code}", follow_redirects=True)
        self.assertEqual(landed.status_code, 200)
        body = landed.get_data(as_text=True)
        self.assertIn("This browser is signed in", body)
        self.assertIn("Exam review", body)
        other_token = self.session_token(other)
        self.assertNotEqual(other_token, token)
        note = other.get("/bookings/1").get_data(as_text=True)
        self.assertIn("HOLD-2291", note)

        again = other.get(f"/handoff/{code}", follow_redirects=True)
        self.assertEqual(again.status_code, 400)
        self.assertIn("already been used", again.get_data(as_text=True).lower())

    def test_expired_handoff_is_rejected(self):
        self.login("lea@campus.edu", "campus123")
        csrf = self.csrf("/me")
        page = self.client.post("/me", data={"csrf_token": csrf}).get_data(as_text=True)
        code = re.search(r"/handoff/([^\"&]+)", page).group(1)
        conn = get_db()
        conn.execute("UPDATE handoff_codes SET expires_at = datetime('now', '-1 minute')")
        conn.commit()
        conn.close()
        stranger = app.test_client()
        response = stranger.get(f"/handoff/{code}")
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(stranger.get_cookie("hold_session"))

    def test_student_books_open_slot_and_logs_out(self):
        self.login("nico@campus.edu", "campus123")
        board = self.client.get("/").get_data(as_text=True)
        action = re.search(r'action="(/slots/\d+/book)"', board).group(1)
        token = re.search(r'name="csrf_token" value="([^"]+)"', board).group(1)
        booked = self.client.post(action, data={"csrf_token": token}, follow_redirects=True)
        self.assertIn("on the list", booked.get_data(as_text=True))

        stolen = self.session_token()
        logout_token = re.search(
            r'name="csrf_token" value="([^"]+)"',
            booked.get_data(as_text=True),
        ).group(1)
        self.client.post("/logout", data={"csrf_token": logout_token}, follow_redirects=True)
        self.assertIsNone(self.client.get_cookie("hold_session"))
        mine = self.client.get("/me", follow_redirects=True).get_data(as_text=True)
        self.assertIn("Sign in", mine)

        replay = app.test_client()
        replay.set_cookie("hold_session", stolen)
        replayed = replay.get("/me", follow_redirects=True).get_data(as_text=True)
        self.assertNotIn("My bookings", replayed)
        self.assertIn("Sign in", replayed)

    def test_registration_is_student_only_and_requires_edu(self):
        token = self.csrf("/register")
        rejected = self.client.post(
            "/register",
            data={
                "email": "ada@gmail.com",
                "display_name": "Ada",
                "password": "campus123",
                "csrf_token": token,
            },
        )
        self.assertIn("Use a .edu email", rejected.get_data(as_text=True))

        token = self.csrf("/register")
        created = self.client.post(
            "/register",
            data={
                "email": "ada@campus.edu",
                "display_name": "Ada Lovelace",
                "password": "campus123",
                "role": "ta",
                "csrf_token": token,
            },
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("Nothing held yet", created)
        self.assertNotIn("Slots you posted", created)
        self.assertNotIn("Post a slot", created)
        blocked = self.client.get("/slots/new")
        self.assertEqual(blocked.status_code, 403)

    def test_ta_posts_slot_and_does_not_see_private_note(self):
        self.login("ravi@campus.edu", "ta-office")
        schedule = self.client.get("/me").get_data(as_text=True)
        self.assertIn("Lea Okonkwo", schedule)
        self.assertNotIn("HOLD-2291", schedule)

        detail = self.client.get("/bookings/1").get_data(as_text=True)
        self.assertIn("lea@campus.edu", detail)
        self.assertNotIn("HOLD-2291", detail)
        self.assertNotIn("Cancel this hold", detail)

        form = self.client.get("/slots/new").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', form).group(1)
        posted = self.client.post(
            "/slots",
            data={
                "csrf_token": token,
                "starts_at": "2026-10-08 15:00",
                "location": "Evans 110",
                "topic": "Debugging lab",
            },
            follow_redirects=True,
        ).get_data(as_text=True)
        self.assertIn("Debugging lab", posted)
        self.assertIn("Slot posted", posted)

    def test_wrong_password_and_student_cannot_post(self):
        token = self.csrf("/login")
        bad = self.client.post(
            "/login",
            data={"email": "lea@campus.edu", "password": "nope", "csrf_token": token},
        )
        self.assertIn("do not match", bad.get_data(as_text=True))
        self.login("lea@campus.edu", "campus123")
        self.assertEqual(self.client.get("/slots/new").status_code, 403)


if __name__ == "__main__":
    unittest.main()
