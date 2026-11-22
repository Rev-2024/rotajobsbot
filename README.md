# Rotary Jobs Connect — chatbot website

A chatbot in the browser where Rotarians post jobs and Rotaractors apply. Menu-driven: people tap buttons and cards, or type short answers. The same conversation logic can also run on WhatsApp (optional, see the end).

## Run it on your computer

Requires Python 3.10+. In a terminal inside this folder:

```
pip install -r requirements.txt
copy .env.example .env          (Mac/Linux: cp .env.example .env)
python -m uvicorn app.main:app --reload
```

Open **http://localhost:8000** — that's the chatbot.

Edit `.env` first (Notepad is fine) and set at least:

| Setting | What to put |
|---|---|
| `ADMIN_PHONES` | The coordinator's mobile number with country code, no `+` (e.g. `919876543210`). Your own number is fine for testing. |
| `SESSION_SECRET` | A long random string (see the comment in `.env.example`). |
| `INVITE_CODE` | Optional. A club code people must enter to create an account. |

Restart the server after changing `.env`.

## First run: try both roles

1. **Coordinator:** *Create account* with the number in `ADMIN_PHONES`. (Skips the invite code.) Pick *I'm a Rotarian*, fill in the details. You'll wait for approval, so send `approve <your number>` (e.g. `approve 919876543210`). Coordinator commands: `pending`, `approve <number>`, `reject <number>`.
2. **Rotaractor:** open a private window (or another browser), create a second account, pick *I'm a Rotaractor*.
3. Post a job as the Rotarian, browse and apply as the Rotaractor, then shortlist from the Rotarian side. Notifications appear in each person's chat within a few seconds.

Type `menu` any time, or use the **Menu** button.

## Put it online (free): Render + Neon

Render runs the app; Neon keeps the data. Do **not** use Render's own free database: it is deleted after 30 days. Free plans change, so check each site's current terms.

**1. Create the database (Neon)**
1. Sign up at neon.com and create a project (pick the region nearest India that is offered).
2. Open the project dashboard and copy the **connection string** (look for a *Connect* button). Use the **direct** connection, not the "pooled" one (its host name contains `-pooler`).
3. It looks like `postgresql://user:password@ep-xxxx.neon.tech/dbname?sslmode=require`. Keep it private: it is a password.

**2. Put the code on GitHub**
1. Create a new **private** repository on github.com.
2. In the project folder: `git init`, `git add .`, `git commit -m "first version"`, then the `git remote add origin ...` and `git push` commands GitHub shows you. (GitHub Desktop works too.)
3. The included `.gitignore` keeps your `.env` file and local databases out of GitHub. Double-check `.env` is not in the repository.

**3. Create the web service (Render)**
1. Sign up at render.com, then **New → Web Service**, and connect your GitHub repository.
2. Settings: Language **Python 3**, Instance type **Free**,
   Build command: `pip install -r requirements.txt`
   Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Add these **environment variables**:

| Name | Value |
|---|---|
| `DATABASE_URL` | The Neon connection string from step 1 |
| `SESSION_SECRET` | A long random string (keep it permanent) |
| `COOKIE_SECURE` | `1` |
| `ADMIN_PHONES` | Coordinator number(s), country code, no `+`, e.g. `919876543210` |
| `INVITE_CODE` | A club code to share with members (recommended) |

   The `.python-version` file in this project tells Render to use Python 3.13.
4. Click **Create Web Service**. When the deploy finishes, open the `onrender.com` address Render shows. That is your chatbot.

**4. First use**
Create the coordinator account (the number in `ADMIN_PHONES`) and test both roles, as in "First run" above. Tables are created automatically on the first start.

**What to expect on the free plan**
- After 15 minutes with no visitors Render puts the app to sleep, and the next visit takes about a minute to wake. Neon also sleeps after 5 minutes idle, which can add a short pause on the first request.
- Your data is safe across restarts and redeploys because it lives in Neon, not on Render.
- Updating: push changes to GitHub and Render redeploys.
- Run a single instance: login-attempt limits are kept in memory.

## How it works

- `app/flows.py` — the whole conversation (registration, job posting, browsing, applying, decisions). Pure: database in, messages out.
- `app/web.py` — website API: sign-up/sign-in (PBKDF2-hashed passwords, locked for 15 minutes after 5 wrong attempts), `/api/chat`, and a notification inbox. Messages for another person (e.g. "new application") are saved and delivered next time their page checks in.
- `app/static/` — the chat page (plain HTML/CSS/JS, no build step; all text is inserted as plain text, never HTML).
- `app/main.py` — wires it together.

## Know the limits

- **Sign-in is number + password, with no OTP.** Anyone could create an account with someone else's number. Rotarians are protected because a coordinator approves each one; for Rotaractors, use `INVITE_CODE` and share it only inside your clubs. Add SMS/WhatsApp OTP later if you need proof of ownership.
- **No password reset yet.** A forgotten password needs a manual fix in the database.
- Notifications arrive while the page is open (checked every 8 seconds) or the next time the user opens it. There are no push or email alerts yet.
- English only. No resume upload. Jobs don't auto-expire at their deadline.

## Tests

```
pytest
```

44 tests: conversations for both roles, permissions (nobody can act on someone else's jobs or applications), sign-in and lockout, notifications, and the page's safety rules.

## Optional: also run on WhatsApp

The WhatsApp connection is still in the code (`app/whatsapp.py`, endpoint `/webhook`) and shares the same users and jobs. To enable it, fill in the `WHATSAPP_*` settings and register `https://<your-site>/webhook` in a Meta developer app (WhatsApp → Configuration, subscribe to *messages*). Note that WhatsApp's 24-hour messaging rule and Meta's message pricing apply there, and that a person using both channels with the same number is one user.
