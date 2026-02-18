# 🔔 IssueBell

> Get a Discord DM the moment a GitHub issue with your chosen label is opened.

GitHub's Watch feature lets you follow entire repositories, but it can't filter by label.  
**IssueBell** bridges that gap: subscribe to a `repo + label` pair and receive a real-time Discord DM whenever a matching issue is created.

---

## Features

- **Label-filtered notifications** — subscribe to any label (`good first issue`, `bug`, `help wanted`, …)
- **Real-time** — powered by GitHub Webhooks, no polling
- **Discord DM** — notifications land directly in your private messages
- **Web UI** — manage subscriptions through a clean dashboard (Discord OAuth2 login)
- **Multi-user** — every user manages their own subscriptions independently

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python · FastAPI · Uvicorn |
| Database | SQLite · SQLAlchemy |
| Auth | Discord OAuth2 (session cookie) |
| Notifications | Discord Bot API (HTTP) |
| GitHub integration | Webhooks (HMAC-SHA256 verified) |
| Frontend | Jinja2 templates · Vanilla JS |

---

## Prerequisites

- Python 3.11+
- A **Discord Application** with a Bot (for sending DMs) and OAuth2 credentials
- A publicly reachable server **or** a tunnel (e.g. [ngrok](https://ngrok.com)) for local development

---

## Setup

### 1. Clone & install

```bash
git clone https://github.com/your-username/IssueBell.git
cd IssueBell
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Create a Discord Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications) → **New Application** → name it "IssueBell"
2. **Bot** tab → **Add Bot** → copy the **Token** → save as `DISCORD_BOT_TOKEN`
3. **OAuth2** tab → copy **Client ID** and **Client Secret**
4. Under **Redirects** add: `http://localhost:8000/auth/callback`
5. Enable the **bot** on your own server so it can DM users  
   *(users must share at least one server with the bot, or have DMs open)*

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">

DISCORD_BOT_TOKEN=<your bot token>
DISCORD_CLIENT_ID=<your client id>
DISCORD_CLIENT_SECRET=<your client secret>
DISCORD_REDIRECT_URI=http://localhost:8000/auth/callback

GITHUB_WEBHOOK_SECRET=<any strong secret string>
```

### 4. Run

```bash
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000)

---

## GitHub Webhook Setup

For **each repository** you want to monitor:

1. Repository **Settings → Webhooks → Add webhook**
2. **Payload URL**: `https://your-domain.com/webhook/github`  
   *(for local dev, use an ngrok URL: `ngrok http 8000`)*
3. **Content type**: `application/json`
4. **Secret**: the value of `GITHUB_WEBHOOK_SECRET` from your `.env`
5. **Which events?** → select **Issues** only
6. **Add webhook**

---

## Project Structure

```
IssueBell/
├── app/
│   ├── main.py              # FastAPI app, startup, Web UI route
│   ├── config.py            # Environment settings (Pydantic)
│   ├── database.py          # SQLAlchemy engine & session
│   ├── models.py            # User, Subscription ORM models
│   ├── schemas.py           # Pydantic request/response schemas
│   ├── routers/
│   │   ├── auth.py          # Discord OAuth2 login / logout
│   │   ├── subscriptions.py # CRUD API for subscriptions
│   │   └── webhook.py       # GitHub webhook receiver
│   ├── services/
│   │   ├── discord.py       # DM sending via Discord Bot API
│   │   └── github.py        # Webhook HMAC signature verification
│   └── templates/
│       └── index.html       # Jinja2 Web UI template
├── static/
│   ├── style.css
│   └── app.js
├── .env.example
├── requirements.txt
└── README.md
```

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Web UI |
| `GET` | `/auth/login` | Redirect to Discord OAuth2 |
| `GET` | `/auth/callback` | OAuth2 callback |
| `GET` | `/auth/logout` | Clear session |
| `GET` | `/subscriptions/` | List current user's subscriptions |
| `POST` | `/subscriptions/` | Add a subscription |
| `DELETE` | `/subscriptions/{id}` | Remove a subscription |
| `POST` | `/webhook/github` | GitHub webhook receiver |

---

## How It Works

```
GitHub Issue opened
       │
       ▼ (HTTP POST)
 /webhook/github
       │
       ├─ Verify HMAC signature
       ├─ Filter: event=issues, action=opened
       ├─ Extract repo + labels from payload
       │
       ▼
  Query SQLite
  "Who subscribed to this repo + any of these labels?"
       │
       ▼ (for each matching user)
  Discord Bot API
  → Open DM channel
  → Send formatted message
```

---

## Discord DM Example

```
🔔 New issue in `facebook/react` (matched label: `good first issue`)
#12345 — Fix typo in useEffect docs
👤 Opened by octocat
🏷️ Labels: good first issue, documentation
🔗 https://github.com/facebook/react/issues/12345
```

---

## License

MIT
