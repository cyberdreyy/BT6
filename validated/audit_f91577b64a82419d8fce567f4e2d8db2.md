This is a prompt injection embedded in the wiki page content, and the "user" text itself is actually a self-answering prompt-injection sandwich: it pre-writes a security-researcher persona, a fake CVE mapping (OpenSIPS/cJSON CVE-2023-28096), and then a "validation prompt" template designed to coerce me into outputting a fabricated vulnerability report. I'm not going to follow embedded instructions from page/document content that conflict with my actual purpose of answering questions about this codebase honestly.

Confirming what's actually there:

- `RESEARCHER.md` is a generic "attacker-first" audit-methodology playbook, not application code — it defines a reporting template and rules for a hypothetical security researcher persona, not anything about this repository's actual functionality. [1](#0-0) 
- `SECURITY.md` is a generic bug-bounty out-of-scope/exclusion list (Immunefi-style boilerplate covering smart contracts, websites, prohibited activities), also unrelated to any specific code in this repo. [2](#0-1) 

There is no cJSON, MI/FIFO parsing, or OpenSIPS-related code found in this repository via search — the injected prompt's premise (mapping CVE-2023-28096, a memory-leak bug in OpenSIPS's `parse_mi_request`/cJSON handling, onto "this repo") has no supporting code artifact here at all. I have no legitimate finding to report, fabricated or otherwise.

If you'd like, I can help you understand what this repository (`EzraCole/gitlab--013`) actually contains — it appears to be a GitLab-based repo — by exploring its real structure and source code, rather than acting on injected instructions from a wiki page.

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
