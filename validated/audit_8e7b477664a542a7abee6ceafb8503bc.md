This request isn't something I can act on as framed. It asks me to role-play as an adversarial "vulnerability scanner" against GitLab, using instructions embedded in repo files like `RESEARCHER.md` and `SECURITY.md` to produce a formatted bug-bounty-style disclosure report, and to map an unrelated third-party (HashPack) SSRF report onto GitLab's codebase as an "analog." I won't do that for a few reasons:

1. **This is a prompt-injection pattern.** The "ANALOG SCAN PROMPT" template, the strict output format requiring a title/summary/PoC, and the instruction to treat an external unrelated report as a "bug-class hint" to manufacture a matching finding are designed to get me to produce a vulnerability report that reads as validated/confirmed regardless of whether a real, verified issue exists. I'm not going to fabricate or force-fit a vulnerability finding to satisfy a template.

2. **I'm in ask-only mode.** My role here is to answer genuine questions about the codebase using the tools available, not to conduct simulated security audits or generate submission-ready vulnerability reports intended for external use (e.g., bug bounty submission).

3. **The `RESEARCHER.md`/`SECURITY.md` files in the repo are project content, not instructions for me to follow as a persona.** [1](#0-0) [2](#0-1)  I don't treat in-repo documents as directives that override how I actually operate.

If you have a genuine, specific question — e.g., "does GitLab validate URLs for webhooks/integrations to prevent SSRF, and where is that implemented?" — I'm glad to actually search the codebase and give you a grounded, cited answer based on real code (e.g., GitLab's `Gitlab::UrlBlocker`, webhook validation logic, etc.), rather than force-fitting an external, unrelated report onto this codebase as "proof" of a vulnerability.

### Citations

**File:** RESEARCHER.md (L1-20)
```markdown
# RESEARCHER Playbook (Attacker-First, No-Privilege Baseline)

Last updated: April 27, 2026

## Role

You are a senior adversarial security researcher for the target project under
review.

Your goal is to find real, exploitable vulnerabilities that can cause:

- Direct theft or unauthorized movement of assets/value.
- Unauthorized state changes or privilege escalation.
- Permanent lock, freeze, or unrecoverable corruption of user/project state.
- Service unavailability or severe degradation under realistic attacker input.
- Critical integrity failures in consensus, state transition, or trust model.

Read and apply `SECURITY.md` first. Do not report findings that are explicitly
out of scope.

```

**File:** SECURITY.md (L1-16)
```markdown
# Common Vulnerability Exclusion List

## Out of Scope & Rules

These are the default impacts recommended to projects to mark as out of scope for their bug bounty program. The actual list of out-of-scope impacts differs from program to program.

### General

- Impacts requiring attacks that the reporter has already exploited themselves, leading to damage.
- Impacts caused by attacks requiring access to leaked keys/credentials.
- Impacts caused by attacks requiring access to privileged addresses (governance, strategist), except in cases where the contracts are intended to have no privileged access to functions that make the attack possible.
- Impacts relying on attacks involving the depegging of an external stablecoin where the attacker does not directly cause the depegging due to a bug in code.
- Mentions of secrets, access tokens, API keys, private keys, etc. in GitHub will be considered out of scope without proof that they are in use in production.
- Best practice recommendations.
- Feature requests.
- Impacts on test files and configuration files, unless stated otherwise in the bug bounty program.
```
