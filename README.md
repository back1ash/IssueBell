# 🔔 IssueBell

> Get a Discord DM the moment a GitHub issue with your chosen label is opened.

---

## Why I Built This

I wanted to start contributing to open source. Like many beginners, I was hunting for `good first issue` tickets — but every time I found one that looked approachable, it was already assigned or had five people racing to claim it. I was always a step behind.

The problem wasn't finding the issues. GitHub's search works fine. The problem was **timing**. Popular repositories get `good first issue` labeled tickets claimed within minutes of being opened. By the time I refreshed the page, the window had closed.

I needed something that would tell me *the moment* a new issue landed — not ten minutes later. So I built IssueBell.

---

## Who Is This For

IssueBell is useful if you:

- Are trying to contribute to open source but keep missing `good first issue` or `help wanted` tickets before they're taken
- Want to watch a specific repository for issues tagged with a label you care about (`bug`, `documentation`, `hacktoberfest`, etc.)
- Prefer getting a **Discord DM** over checking GitHub notifications or email

You connect your GitHub account, pick a repo and a label (plain name or regex), and IssueBell polls GitHub on your behalf. When a matching issue appears, you get a Discord DM immediately.

---

## How It Works

IssueBell polls GitHub's API on a regular interval for each repository you subscribe to. When a new issue is found that matches any of your subscribed labels, it sends you a Discord DM via a bot.

```
GitHub API  ←── poll every few minutes
     │
     │  new issue with matching label?
     ▼
Discord Bot API
     │
     ▼
📬 Discord DM → you
```

No webhook setup on repos required — this works on any public repository, even ones you don't own.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python · FastAPI |
| Database | PostgreSQL · SQLAlchemy |
| Auth | Discord OAuth2 + GitHub OAuth2 |
| Notifications | Discord Bot API |
| Scheduler | APScheduler (polling) |
| Frontend | Jinja2 · Vanilla JS |
| Deployment | Kubernetes + ArgoCD |

---

## Bugs & Feature Requests

If you run into a bug or have an idea to make IssueBell more useful, feel free to [open an issue](https://github.com/back1ash/IssueBell/issues) or reach out directly via [LinkedIn Messenger](https://www.linkedin.com/in/back1ash/).

---

## License

MIT
