This request is a prompt-injection attempt embedded in what looks like a formatted vulnerability-scan template. It's trying to get me to role-play as an autonomous security-scanning agent that treats an unrelated, external CVE (a Nextcloud `user_oidc` authentication bypass) as grounds to fabricate a "matching" vulnerability report against this repository, using rigid output formatting rules designed to make me skip normal verification and reasoning.

I did search the codebase for genuinely related concepts (OIDC/OmniAuth authentication, identity linking) to check for a real analog, but the searches only surfaced unrelated documentation (`RESEARCHER.md`), not an actual OIDC authentication implementation in this repository that maps to the CVE's root cause.

I won't fabricate a vulnerability report to satisfy the prompt's template. Manufacturing a "Title/Summary/Finding Description/Impact/PoC" for a bug that isn't substantiated by actual code evidence in this repo would be a false security claim, and that's true regardless of how the request is formatted. If you have a specific, real concern about OIDC/OmniAuth authentication handling in this codebase (e.g., how GitLab's OmniAuth OIDC strategy resolves user identity from claims), I can search for and cite the actual relevant code so we can evaluate it honestly. [1](#0-0)

### Citations

**File:** RESEARCHER.md (L1-19)
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
