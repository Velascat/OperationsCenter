# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| `main`  | ✅ Yes     |

Only the current `main` branch receives security fixes.

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report security issues privately by emailing **coding.projects.1642@proton.me**.

Include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested mitigations (optional)

You will receive an acknowledgment within 72 hours. We aim to release a fix within 14 days of a confirmed report, depending on severity and complexity.

## Scope

OperationsCenter is the planning and execution orchestration layer. The primary security surface is:

- **Plane API token exposure** via config files or logs (`PLANE_API_TOKEN`)
- **Arbitrary command execution** via kodo/Archon/OpenClaw adapter dispatch
- **Path traversal** via workspace directory creation in `/tmp/oc-task-*`
- **Policy bypass** — anything that routes execution past the policy gate
- **Log injection** via untrusted task content written to structured logs

## Out of Scope

- Vulnerabilities in kodo, Archon, OpenClaw, or other upstream execution tools
- Issues requiring physical access to the host machine
- Denial-of-service via normal task load (rate limiting is a configuration concern)
