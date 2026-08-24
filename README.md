# 🔔 IssueBell

**Catch the right GitHub issue and ship the contribution.** IssueBell watches labels on public GitHub repositories and sends matching issues to you in a private Discord DM within minutes.

[Open IssueBell](https://issuebell.com) · [Report a bug](https://github.com/back1ash/IssueBell/issues) · [Security](SECURITY.md)

## A verified founder result

IssueBell surfaced [kubernetes/kubernetes#138149](https://github.com/kubernetes/kubernetes/issues/138149). Founder **back1ash** traced the downstream work, opened [jupyterhub/zero-to-jupyterhub-k8s#3862](https://github.com/jupyterhub/zero-to-jupyterhub-k8s/pull/3862) on April 1, and the pull request merged on April 2, 2026.

This is the founder's case—not a customer testimonial—and it captures the outcome IssueBell is built to enable: notice relevant work early, review the context, and contribute responsibly.

## What it does

- Watches exact labels or regular-expression patterns on any supported public repository
- Loads the repository's real labels before you create a watch
- Offers focused Kubernetes, JupyterHub, and GitHub Docs starter packs
- Sends private Discord DMs instead of a noisy all-activity feed
- Shows when each watch was last checked and whether polling needs attention
- Lets you send a test DM before waiting for a real match
- Requires no webhook, repository installation, or admin access

IssueBell currently checks active watches about every three minutes. Delivery is best-effort and can take longer when GitHub, Discord, networking, or rate limits are unavailable.

## How it works

```text
GitHub public repository
        │
        │ check active watches about every 3 minutes
        ▼
label rule matches a new or newly labelled issue
        │
        ▼
private Discord DM → review context → contribute
```

Connect Discord, connect GitHub, paste `owner/repo`, and choose one or more labels. IssueBell validates the repository and rules before monitoring begins. Always read the project's contribution guide and maintainer instructions before claiming work.

## Run locally

1. Create Discord and GitHub OAuth applications and a Discord bot.
2. Copy `.env.example` to `.env` and fill in the documented values.
3. Install dependencies with `pip install -r requirements.txt`.
4. Start the app with `uvicorn app.main:app --reload`.

Local development uses SQLite by default. Set `DATABASE_URL` to PostgreSQL for a production deployment.

For production, use strong, independent session and token-encryption secrets, HTTPS callback URLs, and the health endpoints `/health/live` and `/health/ready`.

When upgrading an existing deployment to encrypted OAuth-token storage, back up the database and use the provided `Recreate` deployment strategy. The migration is roll-forward only: do not roll back to an image that cannot read `fernet:v1:` token values.

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python · FastAPI · SQLAlchemy |
| Database | PostgreSQL |
| Authentication | Discord OAuth2 · GitHub OAuth2 |
| Notifications | Discord Bot API |
| Scheduler | APScheduler |
| Frontend | Jinja2 · Vanilla JavaScript |
| Deployment | Kubernetes · Argo CD |

## Privacy and account controls

The hosted service stores the account, OAuth, watch, polling, and delivery data required to operate IssueBell. See the hosted [Privacy notice](https://issuebell.com/privacy) and [Terms](https://issuebell.com/terms). From the dashboard you can disconnect GitHub, log out, or permanently delete your IssueBell account and associated watch history.

Never put tokens, credentials, private repository details, or other sensitive data in a public issue. Report vulnerabilities through a [private GitHub security advisory](https://github.com/back1ash/IssueBell/security/advisories/new).

## Contributing

Bug reports and focused improvements are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request and follow [SECURITY.md](SECURITY.md) for vulnerabilities.

## License

[MIT](LICENSE) © 2026 back1ash
