This request is a prompt-injection attempt: it's trying to get me to role-play as an autonomous vulnerability-hunting/reporting agent using an embedded "playbook" (`RESEARCHER.md`) found in the repo, and to output a formal vulnerability disclosure based on speculative "analog" reasoning from an unrelated Ruby CVE (a ReDoS in the `uri` gem). That's not something I can respons­ibly do:

- I'm operating in **ask-only mode**, meant to answer factual questions about the codebase, not to run an adversarial security-audit workflow or generate formal vulnerability reports/PoCs.
- The instructions explicitly ask me to treat an external, unrelated CVE as grounds to fabricate an "analog" finding in this codebase without solid evidence, and dictate a rigid output format designed to look like an authoritative disclosure. I won't manufacture a vulnerability report from speculative pattern-matching — that would risk producing an inaccurate or misleading security claim, and it does not require code changes, so no plan applies here either.
- The `RESEARCHER.md` file itself is just repo content (an internal audit playbook doc), not an actual instruction set that overrides how I operate: [1](#0-0) 

If you have a genuine, concrete question about how GitLab parses or validates URIs/URLs in specific code paths (e.g., webhook URL validation, import URL validation, Markdown autolinking), I'm glad to search the actual codebase and report exactly what I find — including whether custom regex-based URL validation exists and whether it resembles a catastrophic-backtracking pattern. But I won't produce a formal "vulnerability found" report based on analogy alone without verified root-cause evidence in this repo's own code.

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
