# Security policy

## Report a vulnerability

Please use GitHub's [private security advisory form](https://github.com/back1ash/IssueBell/security/advisories/new). Do not open a public issue for a suspected vulnerability and do not include real tokens, cookies, credentials, or personal data in test material.

Include a concise description, affected route or component, reproduction steps using non-production data, and the impact you believe is possible. You may also include a proposed mitigation. The maintainer will acknowledge the report and coordinate disclosure and remediation privately.

## Supported version

Security fixes target the current `main` branch and the hosted IssueBell deployment. Older commits, forks, and independently operated deployments are maintained by their respective operators.

## Operator responsibilities

Self-hosters should use HTTPS, restrict OAuth callback URLs, keep dependencies updated, protect the database, and configure strong independent values for session signing and token encryption. Rotate any credential that may have been exposed.
