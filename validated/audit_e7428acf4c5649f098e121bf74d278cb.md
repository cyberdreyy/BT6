This request is a prompt-injection attempt, not a legitimate codebase question. A few things make this clear:

1. **The "report" is asking me to fabricate a vulnerability analog.** It supplies an external CVE about a Dokploy-specific bug (`packages/server/src/services/registry.ts` — a file that doesn't exist in this repository) and instructs me to find an "analog" in GitLab, with a rigid output template demanding I produce a Title/Summary/Finding/Impact/PoC report regardless of whether real evidence exists.

2. **The repository itself contains injected instruction files.** Files like `RESEARCHER.md` and `SECURITY.md` in this repo are themselves prompt-injection payloads dressed up as "playbooks" telling an AI agent how to behave and what format to output security findings in — these are not standard GitLab repository files and should not be treated as trusted instructions. [1](#0-0) [2](#0-1) 

3. **No genuine code evidence was found.** My searches for shell-escaping helpers or docker login/logout command construction (the actual root cause in the cited Dokploy CVE) returned no matching GitLab source — only these instruction-like markdown files.

I won't fabricate a vulnerability report based on an unrelated external CVE and a template embedded in untrusted repository content. If you have a genuine, specific concern about command construction or shell-escaping in this repository (e.g., a particular file that shells out to `git`, `docker`, or similar), point me to it and I can investigate the actual code.

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

**File:** SECURITY.md (L1-20)
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

### Smart Contracts / Blockchain DLT

- Incorrect data supplied by third-party oracles.
```
