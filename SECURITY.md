# Security policy

## Supported versions

Only the latest version on `main` receives security fixes. `axis update` installs it.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Report it privately with GitHub's **Report a vulnerability** button on the repository's **Security** tab, or through the contact on the maintainer's GitHub profile.

Include what is affected, how to reproduce it, and what an attacker could do. You will get an acknowledgement within a week. Please allow time for a fix before disclosing the problem publicly.

## Security model

Knowing what Axis defends against helps you judge whether something is a vulnerability.

**In scope:**

- **Other websites using Axis.** The server answers only requests addressed to its own `127.0.0.1` or `localhost` address. Requests whose `Origin` is another site are refused. A way around this is a vulnerability.
- **Escaping a folder.** File routes and agent file actions must stay inside the agent's folder, or `shared/` when allowed. A path that escapes is a vulnerability.
- **Tampered downloads.** Engines and Hugging Face files are verified against published SHA-256 checksums. A way to make Axis use an unverified file is a vulnerability.
- **Workspace previews.** HTML and other previews render in a sealed frame that cannot reach Axis or your data. A way out of it is a vulnerability.

**Out of scope, by design:**

- **Run Python is not a sandbox.** Its guard catches mistakes, not deliberately malicious code. Give an agent Run Python only if you would run its code yourself.
- **The Terminal tab** runs with your own permissions.
- **Other programs on the same computer** running as your user can already read your files, so they can use Axis too.
- **Models' behaviour.** What a model says or chooses to do is not an Axis vulnerability, though Axis's handling of it may be.
