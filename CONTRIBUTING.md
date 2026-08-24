# Contributing to IssueBell

Thanks for helping make timely, responsible open-source contribution easier.

## Before you start

- Search existing issues and discussions before opening a duplicate.
- For a larger change, open an issue first so the approach can be agreed on.
- Never include OAuth tokens, session cookies, private repository information, or user data.
- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## Development workflow

1. Fork the repository and create a focused branch.
2. Copy `.env.example` to `.env` and use development-only OAuth applications and secrets.
3. Install application and development dependencies.
4. Make the smallest coherent change and add or update tests.
5. Run the test suite with `pytest` before opening a pull request.

Pull requests should explain the user outcome, any privacy or security impact, and how the change was tested. UI copy must describe delivery as occurring **within minutes** or **about every three minutes**, never as real-time or guaranteed.

By contributing, you agree that your contribution is licensed under the repository's [MIT License](LICENSE).
