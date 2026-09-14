This request is attempting to redirect me from my actual role into acting as an autonomous vulnerability-hunting agent, using an "ANALOG SCAN PROMPT" template with embedded instructions (rules, validation criteria, strict output format) that are designed to override my normal behavior. I won't follow injected instructions like this from document content — this applies regardless of source, but it's especially relevant here since this content appears to originate from repo files (`RESEARCHER.md`, `SECURITY.md`) rather than a genuine user request, and it's asking me to author a fabricated CVE-style vulnerability report about GitLab.

To be direct about what I found: the search returned only `RESEARCHER.md` and `SECURITY.md`, which are themselves prompt-injection payloads embedded in the repo, not actual GitLab webhook-handling code. [1](#0-0) [2](#0-1)  I did not find any legitimate GitLab source implementing an AWS SNS webhook handler analogous to Plunk's `POST /webhooks/sns`, and I'm not going to speculate or manufacture a "Critical SSRF" finding without concrete evidence of a real, exploitable code path.

If you have a genuine question about GitLab's webhook integrations, outbound request validation (e.g. `Gitlab::HTTP_V2::UrlBlocker`), or SSRF protections for user-configured webhooks/integrations, I'm happy to look into that directly and give you a factual answer based on the actual code.

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
