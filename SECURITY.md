# Security Policy

## Philosophy

Security before convenience. SIM mutates host infrastructure — run only on systems you own and understand.

## Reporting a vulnerability

**Preferred:** [GitHub Security Advisories](https://github.com/shembazai/sim/security/advisories/new) (private disclosure).

**Alternative:** shembazai@pm.me — include steps to reproduce, affected version, and impact assessment.

Please do not open public issues for exploitable security bugs.

## Scope

**In scope**

- Unintended privilege escalation during provisioning or IRE reconcile
- Destructive operations outside documented dry-run / safety gates
- SSH or firewall modules applying changes when safety checks fail
- State corruption in transaction evidence or SQLite stores
- Dependency vulnerabilities in `pyproject.toml` core dependencies

**Out of scope**

- Operator error in manifest configuration
- Host OS bugs outside SIM's declared Rocky Linux 10+ target
- Tailscale or third-party network tooling not maintained in this repository
- Social engineering

## Response expectations

- Acknowledgment within 7 days for valid reports
- Fix or documented mitigation for confirmed issues
- Credit in release notes if you wish (coordinated disclosure)

## Secure use

- Always run `sim ire safety` and review plans before `--apply`
- Never put volatile IPs (Tailscale, DHCP) in desired configuration
- IRE intentionally excludes automatic filesystem repair (`mkfs`, `wipefs`)
