# IssueBell privacy notice

Last updated: September 24, 2026

IssueBell stores the data needed to authenticate accounts, check chosen watch rules, retry and deduplicate delivery, and send Discord notifications. This can include Discord and GitHub account identifiers, usernames and avatar references, an encrypted GitHub OAuth token, repository and label rules, polling and recent test-delivery records, server-side login sessions, and a small allowlisted set of timestamped setup events (for example, first watch created).

The data is used to operate and secure IssueBell, diagnose delivery failures, and respond to support requests. It is not sold or used for advertising. GitHub processes repository requests and Discord processes authentication and direct-message delivery under their respective policies.

A non-identifying browser cookie is kept for up to one year to show a sign-in-again screen when a login expires. It grants no account access and is removed on logout or account deletion.

Account data remains until it is deleted or is no longer needed to operate and protect the service. Only the 20 most recent test-delivery diagnostics per account are kept. A signed-in user can disconnect GitHub without deleting the Discord account, or use **Delete account** in the dashboard to remove the IssueBell account and its associated sessions, setup events, watches, and delivery records. IssueBell also attempts to revoke the connected GitHub token when the account is deleted.

For security reports, use the [private GitHub advisory form](https://github.com/back1ash/IssueBell/security/advisories/new). For privacy questions, contact the maintainer through the repository without posting sensitive information publicly. The hosted version of this notice is available at [issuebell.com/privacy](https://issuebell.com/privacy).
