# Twilio Bulk SMS Demo

A FastAPI backend plus a Streamlit frontend that let an authorized user pick department contacts (individually or by group) and send them a bulk SMS via Twilio.

## Architecture

- `main.py` — FastAPI backend. Talks to Twilio. Exposes:
  - `POST /api/auth/login` — verify a 6-digit authenticator app code (TOTP), returns a session token valid for 15 minutes.
  - `POST /api/auth/logout` — invalidate a session token.
  - `GET /api/contacts` — list all groups/contacts from `contacts.json`. Public (read-only, no secrets exposed).
  - `GET /api/phone-numbers` — list the Twilio numbers on your account (friendly name + E.164 number), cached for 5 minutes. Public (read-only).
  - `POST /api/contacts` — add a contact to a group (creates the group if it doesn't exist). **Requires a valid session token.** Persists to `contacts.json`.
  - `DELETE /api/contacts` — remove a contact from a group. **Requires a valid session token.** Persists to `contacts.json`.
  - `POST /api/contacts/bulk` — bulk-add contacts from a parsed Excel file. **Requires a valid session token.** Invalid/duplicate rows are skipped individually and reported back, not the whole import.
  - `POST /api/send` — enqueues a Celery task to send the batch via Twilio and returns immediately with a `taskId`. **Requires a valid session token.**
  - `GET /api/send/status/{task_id}` — poll a send task's progress/result. **Requires a valid session token.**
- `celery_app.py` / `tasks.py` — Celery app and the `send_sms_batch` background task that actually talks to Twilio.
- `setup_totp.py` — one-time script that generates your authenticator secret and an enrollment QR code.
- `app.py` — Streamlit frontend with two tabs: **Send Message** and **Manage Contacts**, plus a login panel and dark/light mode toggle in the sidebar. Calls the FastAPI backend over HTTP. No Twilio logic or secrets live here. Contacts are cached client-side for 30s to keep the UI snappy, and the cache is cleared automatically after any add/remove. Both tabs have a search box (matches name, phone, or email) that filters the group/contact list.

You run the backend, the Celery worker, and the frontend at the same time (three processes, or one `docker compose up`).

## Why Redis + Celery

Originally `/api/send` looped through every recipient and called Twilio synchronously inside the HTTP request — under concurrent load (many users sending at once, or one big batch), that ties up a request thread for the entire duration of the loop, which is what causes a server to fall over under heavy traffic. Two changes fix this:

- **Celery** moves the actual Twilio calls into a background worker process. `/api/send` now just enqueues the job and returns instantly; the frontend polls `/api/send/status/{id}` for the result. The backend's request-handling capacity is no longer coupled to how many recipients are in a batch or how slow Twilio's API is.
- **Redis** backs three things:
  - The Celery broker/result store (how the backend hands jobs to the worker and reads results back).
  - Session storage — sessions used to live in an in-process Python dict, which breaks the moment you run more than one backend worker process (a login on one worker wouldn't be recognized by another). Redis makes sessions shared and survives backend restarts.
  - A short-lived (10s) cache for `GET /api/contacts`, so a burst of concurrent requests hits Redis instead of re-reading `contacts.json` off disk every time.

## Setup

1. Copy `twilio.env.example` to `twilio.env` and fill in your real values:
   - `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` — from the [Twilio Console](https://console.twilio.com).
   - `TOTP_SECRET` — leave blank, generated in step 4 below.
   - `API_BASE_URL` — must match the port you run the backend on (e.g. `http://localhost:8000`).
   - There's no `TWILIO_PHONE_NUMBER` setting — the app fetches every number on your Twilio account and lets you pick one (by friendly name) from a dropdown when sending. Make sure each number on your account has a friendly name set in the Twilio Console (Phone Numbers → Manage → Active Numbers) so they're identifiable in the dropdown.

2. Copy `contacts.example.json` to `contacts.json` and edit it with real department contacts and groups. (`contacts.json` is gitignored since it holds real phone numbers/emails — only the example file is committed.)

3. Create a virtual environment and install dependencies:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

4. Set up the authenticator app login (one time only):
   ```
   python setup_totp.py
   ```
   This writes `TOTP_SECRET` into `twilio.env` and saves `totp_qr.png` in the project folder. Open that image and scan it with Google Authenticator, Microsoft Authenticator, or Authy — or manually enter the secret key it prints. From then on, that app shows a new 6-digit code every 30 seconds, which is what you'll type in to log in.

5. Start Redis (needed by the backend, the worker, and as the Celery broker). Easiest is a standalone container:
   ```
   docker run -d --name redis -p 6379:6379 redis:7-alpine
   ```
   Or install Redis natively and run `redis-server`. Either way, `REDIS_URL=redis://localhost:6379/0` in `twilio.env` should point at it.

6. Run the backend (in one terminal):
   ```
   uvicorn main:app --reload --port 8000
   ```
   (If port 8000 is blocked on Windows, pick another free port and update `PORT`/`API_BASE_URL` in `twilio.env` to match.)

7. Run the Celery worker (in a second terminal, with the venv activated):
   ```
   celery -A celery_app worker --loglevel=info
   ```
   (On Windows, Celery's default worker pool can be flaky — if you hit issues, add `--pool=solo`.)

8. Run the frontend (in a third terminal, with the venv activated):
   ```
   streamlit run app.py
   ```

9. Streamlit opens a browser tab (default `http://localhost:8501`).
   - In the sidebar, enter the current 6-digit code from your authenticator app and click **Verify & Log In**. This logs you in for 15 minutes.
   - **Send Message tab**: pick recipients/groups, choose which Twilio number to send from (by friendly name) in the **Send from** dropdown, write a message, and send.
   - **Manage Contacts tab**: add a new contact (name, country code + phone digits, optional email) to an existing or brand-new group, or remove an existing one. Changes are written directly to `contacts.json`.
   - **Bulk import**: in the Manage Contacts tab, download the Excel template, fill in rows with columns `Group`, `Name`, `Phone`, `Email` (Email optional), then upload it and click **Import Contacts**. Bad rows (invalid phone, duplicate, missing fields) are skipped individually with a reason — the rest still import.
   - Use the **search box** at the top of either tab's contact list to filter by name, phone, or email.
   - Toggle **🌙 Dark mode** at the top of the sidebar to switch themes.
   - Click **Log out** in the sidebar to end the session early.

## Running with Docker

This skips the manual venv/Redis/uvicorn/celery/streamlit steps above — everything runs in four containers instead.

1. Complete steps 1, 2, and 4 above first (you still need a real `twilio.env` with `TOTP_SECRET` set, and a real `contacts.json` — both stay on your host machine, never baked into the image). You do **not** need to run Redis yourself — Compose provides it.

2. Build and start everything:
   ```
   docker compose up --build
   ```
   This starts:
   - `redis` — the Redis instance used for sessions, contacts caching, and the Celery broker/backend.
   - `backend` — the FastAPI app on `http://localhost:8000`.
   - `worker` — the Celery worker that actually sends the Twilio messages in the background.
   - `frontend` — the Streamlit app on `http://localhost:8501`, configured to reach the backend at `http://backend:8000` over the Docker network.

3. Open `http://localhost:8501` and log in as usual.

4. Stop everything with `Ctrl+C`, or `docker compose down`.

Notes:
- `contacts.json` is volume-mounted into the backend container, so edits made through the app persist back to your host file, not just inside the container.
- `twilio.env` is passed in via `env_file`, not copied into the image — secrets never end up baked into a Docker layer. `REDIS_URL` is overridden in `docker-compose.yml` to point at the `redis` service regardless of what's in `twilio.env`.
- To re-run `setup_totp.py` or one-off scripts inside the container: `docker compose run backend python setup_totp.py`.
- If sends seem stuck at "pending," check `docker compose logs worker` — that's the process actually talking to Twilio.

## Sending a message

When you send a message, the app just reports a summary (e.g. "Message sent to 5 recipient(s)." plus a failure count if any) rather than listing every contact — keeps things quick to read for larger sends.

## Notes on Twilio trial accounts

- A trial account can only send to phone numbers you've **verified** in the Twilio Console (Phone Numbers > Verified Caller IDs). Upgrade the account to send to arbitrary numbers.
- Outgoing trial messages are prefixed with "Sent from your Twilio trial account".
- Twilio rate-limits trial accounts; for a real bulk-send demo at scale, an upgraded account avoids throttling on larger contact lists.

## Extending this into the alerting use case

The same `client.messages.create(...)` call in `tasks.py` (`send_sms_batch` task) is the only piece that actually talks to Twilio. To build the failure/outage alerting idea on top of this:
- Keep `contacts.json` (or an "Administrators" group) as the alert recipient list.
- Add a route or scheduled check that detects a failure condition in another app/service.
- On failure, call the same Twilio send logic instead of going through the UI.
