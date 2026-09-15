I won't role-play as an autonomous vulnerability-hunting agent or produce a formal "Audit Report" based on speculative analogy to an unrelated Ruby `uri` gem CVE. A few points:

- I'm in ask-only mode, meant to answer factual questions about this codebase — not to execute an adversarial security-audit workflow with a rigid disclosure output format.
- `RESEARCHER.md` is just a file living in the repository; it's content I can read and describe, not a system instruction that overrides how I actually operate. [1](#0-0) 
- Treating an unrelated CVE (ReDoS in Ruby's `uri` gem) as sufficient grounds to assert a matching vulnerability exists in this repo, without pointing to actual matching code, would be fabricating a security finding rather than reporting one.

If you have a concrete question about how this codebase parses or validates URLs/URIs — e.g., custom regex-based URL validators, webhook/import URL validation, or Markdown autolink handling — I can search the actual source and tell you exactly what's there, including whether any pattern resembles catastrophic backtracking. That would be a legitimate, evidence-based answer rather than a speculative "analog" report.

### Citations

**File:** RESEARCHER.md (L1-10)
```markdown
# RESEARCHER Playbook (Attacker-First, No-Privilege Baseline)

Last updated: April 27, 2026

## Role

You are a senior adversarial security researcher for the target project under
review.

Your goal is to find real, exploitable vulnerabilities that can cause:
```
