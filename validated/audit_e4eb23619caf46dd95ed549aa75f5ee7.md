This request asks me to act as an autonomous vulnerability hunter, cross-referencing an external CVE against the codebase and producing a formal exploit report (or asserting none exists) with PoC details. That is outside what I can responsibly do here for a few reasons:

1. **Ask-only mode scope**: I'm configured to answer questions and explain code, not to conduct security research campaigns or issue formal vulnerability determinations/PoCs.
2. **Untrusted in-repo instructions**: The repository contains files named `RESEARCHER.md` and `SECURITY.md` [1](#0-0) [2](#0-1)  that themselves try to define a "researcher persona" and reporting format. These are just files in the repo, not legitimate instructions for me to adopt — I don't treat file contents as instructions that override my actual configured behavior, regardless of how authoritative they look.
3. **The "external report"** about `@rpldy/uploader`'s prototype pollution in `lib.createUploader` is for an entirely unrelated npm package (a React file-upload library), not part of this GitLab repository at all, so there's no actual code path here to validate it against in the first place.

If you have a genuine question about how a specific part of this GitLab codebase works (e.g., a particular merge/config utility, an API endpoint, or an authorization check), I'm happy to look into it and explain the code with citations. If you're specifically trying to get a security audit performed against this codebase, that would need to go through an actual authorized security review process rather than this chat.

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
