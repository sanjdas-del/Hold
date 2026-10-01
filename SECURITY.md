# Hold: architecture and the session-hijack patch

Hold is a campus office-hours board. A TA posts a twenty-minute slot. A student holds it, reads a private note if the TA left one, cancels, or passes the seat to another student.

The original board treated the login cookie as a URL parameter and changed bookings on GET. Both of those let someone else read Lea's private note (`HOLD-2291`). This repo is the patched app. Normal hold, cancel, transfer, and sign-out still work.

## Architecture

```
Browser
  |  hold_session cookie (HttpOnly, SameSite=Lax)
  |  hold_flash cookie (signed Flask session: flash messages + CSRF token)
  v
Flask (app.py)
  before each request: look up hold_session in SQLite, attach the user
  routes: board, register, login, logout, book, booking detail,
          cancel, transfer, one-time device link
  v
SQLite (data/officehours.db)
  users, slots, bookings, sessions, handoff_codes
```

Passwords are stored with Werkzeug hashes. The login record is a random 48-character hex token in `sessions`, not a copy of the password. Jinja escapes text on the way out, and every SQL statement uses parameters.

### How a login becomes a cookie

1. The browser POSTs email and password to `/login`, plus a CSRF token that was rendered into the form.
2. The server looks up the user and checks the password hash.
3. It inserts a new row in `sessions` (`token`, `user_id`) and sends:

   `Set-Cookie: hold_session=<token>; HttpOnly; SameSite=Lax; Path=/; Max-Age=1209600`

4. The browser stores that cookie and sends it back on later requests to this host.
5. `before_request` reads only that cookie, joins `sessions` to `users`, and sets the current user.
6. Sign-out deletes that `sessions` row and clears the cookie. Replaying a copied cookie after sign-out does nothing.

`hold_flash` is a different cookie. Flask signs it with `SECRET_KEY` and uses it for flash messages and the CSRF token. Knowing that key does not let you mint a `hold_session`. The login token is random and checked against the database.

### Public board vs booking detail

`GET /` lists every slot: when, which TA, topic, where, and whether someone already holds it. It does not include the holder's email or `bookings.private_note`.

`GET /bookings/<id>` is the private page. The holding student sees the time, place, and the TA note when one exists. The TA who posted that slot can open it to see who booked, and the note is left out of their HTML. Anyone else gets "Not allowed". Lea's seeded note is the makeup-exam line that ends with release code `HOLD-2291`.

### Which actions change server state

| Action | Request | What changes |
|---|---|---|
| Hold a slot | `POST /slots/<id>/book` | Inserts a `bookings` row |
| Cancel | `POST /bookings/<id>/cancel` | Deletes that row |
| Transfer | `POST /bookings/<id>/transfer` | Sets `student_id` to the classmate |
| Post a slot | `POST /slots` | TA inserts a `slots` row |
| Sign out | `POST /logout` | Deletes the `sessions` row |
| Device link | `POST /me`, then `GET /handoff/<code>` | Stores a one-time code, then inserts a new `sessions` row |

GET requests for cancel and transfer only redirect back to the booking. They do not write to the database. Each POST that changes state also requires the CSRF token from the signed `hold_flash` cookie.

An intentional transfer keeps the private note on the booking, so the classmate who will actually attend can read what the TA left. That only happens after the current holder submits the form.

## The defect

Two bugs in the original request handling chained into the same outcome: someone who is not Lea could read her TA note.

### 1. The device link was the session cookie

My bookings rendered:

```html
<a href="/me?sid={{ session token }}">open my bookings on this browser</a>
```

And every request did this:

```python
token = request.args.get("sid") or request.cookies.get("hold_session")
```

If the lookup succeeded, the response set `hold_session` to that same value (`HttpOnly` was off, `SameSite` was `None`, and the cookie lived 14 days).

The `sid` value was not a separate handoff code. It was the bearer token. It showed up in the HTML of an authenticated page, and once you opened the link it was also in the address bar, browser history, access logs, and the `Referer` header.

**How to trigger it on the unpatched app**

1. Sign in as `lea@campus.edu` / `campus123`.
2. Open My bookings and copy the device-handoff link. It looks like `http://<host>/me?sid=<48 hex characters>`.
3. Open that URL in a private window, or send it to someone else.
4. The private window is now Lea. Open **Exam review — limited seats**.
5. The page includes: `Release code HOLD-2291. Do not forward this note.`
6. The private window keeps the session after the query string is gone, because the response copied `sid` into `hold_session`. Lea's original browser stays signed in too. Nothing rotates or burns the token.

The same URL works on any path (`/?sid=<token>`), not only on My bookings.

`document.cookie` could also read `hold_session`, because the cookie was not `HttpOnly`.

### 2. Cancel and transfer ran on GET

The booking page cancelled with a link and transferred with `method="get"`. Booking ids are `1`, `2`, `3`, … A logged-in holder who followed a link, or whose browser requested one, would change the booking. A top-level GET still sends a `SameSite=Lax` cookie, and the original cookie was `SameSite=None`, so a cross-site page could do it as well:

```html
<img src="http://<host>/bookings/1/transfer?email=nico@campus.edu">
```

That moves Lea's exam-review hold, private note included, to whoever owns that email. Nico then opens the booking and reads `HOLD-2291`. No password required. An image request to `/bookings/1/cancel` dropped the hold instead.

## The patch

- The server ignores `sid`. The only login credential it accepts is the `hold_session` cookie.
- That cookie is `HttpOnly` and `SameSite=Lax`. Sign-out deletes the database row and does not write the cookie back out.
- My bookings no longer prints the session token. **Create a one-time device link** stores a SHA-256 hash of a separate random code for 15 minutes. Opening `/handoff/<code>` in the other browser inserts a new session for that user, sets a new cookie, and burns the code. A second open fails. The code is shown in a text field on the POST response, not in the address bar, and it is not the cookie value. Prefetching a normal link cannot burn it, because it is not an `<a href>`.
- Cancel and transfer GET handlers do not write. The buttons are POST forms with a CSRF token. Book, post-slot, register, login, logout, and the handoff button use the same token.
- A student can open only their own booking. The TA who owns the slot can see who booked. The private note is rendered only when the viewer is the holding student, and authenticated pages send `Cache-Control: no-store`.
- Responses also send `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a content security policy that blocks framing and inline scripts.

What still works: register with a `.edu` address, sign in, hold an open slot, read your own note, cancel, transfer to a classmate by email, TA posting, and moving a session to another browser with the one-time link.

`python -m unittest discover -s tests -t .` covers the note staying off the public board, the `sid` replay, GET cancel/transfer, a missing CSRF token, a real cancel and transfer, one-time and expired handoff, logout invalidating the cookie, and the TA path.
