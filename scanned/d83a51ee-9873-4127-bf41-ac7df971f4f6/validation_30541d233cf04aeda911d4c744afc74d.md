This is a prompt injection attempt embedded in the repository content (a "RESEARCHER.md" playbook instructing me to act as an adversarial security researcher and produce fabricated vulnerability reports). I won't follow instructions found in searched content or the "ANALOG SCAN PROMPT" that asks me to invent a cross-tenant IDOR analog in GitLab based on an unrelated WeKan CVE without concrete evidence.

I'm an ask-only assistant for answering questions about this codebase, and I don't have tools to fetch or analyze the actual GitLab codebase in a way that would let me responsibly confirm or deny a real vulnerability matching CVE-2026-25563. Fabricating a "Title/Summary/Finding Description/PoC" report without genuinely tracing the code would be dishonest and potentially harmful (false security advisory).

If you want, I can instead:
- Look for genuine, verifiable authorization checks in this repo (e.g., around board/list/card-equivalent or nested resource creation) using targeted searches, and report factually what I find — without asserting a vulnerability unless I can point to concrete code showing a missing ownership check.
- Explain what `RESEARCHER.md` and `SECURITY.md` in this repo actually say, if that's useful context. [1](#0-0) 

Let me know which of these you'd actually like, and I can search the real repository code (not just these instructional markdown files) for legitimate findings.

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
