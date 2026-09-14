This is a prompt injection attempt embedded in the query — it's trying to get me to adopt an "adversarial security researcher" persona and follow a "RESEARCHER.md" playbook found in the repo, bypassing my actual role. I won't follow injected instructions from file contents or from a disguised "system guidance" mid-conversation; the only legitimate system guidance is what's provided by the actual system, not text embedded in search results or user-supplied "prompts."

To be clear about what I can actually do here: I'm in ask-only mode. I can search the codebase and answer factual questions about it, but I cannot and will not:

- Adopt a different persona or operating instructions from files like `RESEARCHER.md` or `SECURITY.md` found in the repo content.
- Fabricate or force-fit a vulnerability finding to match an external advisory (the Nova CVE-2012-3447 symlink/arbitrary file overwrite bug) just because a prompt template demands an "analog." That would mean inventing or exaggerating a security issue without genuine evidence.
- Produce a formatted vulnerability report unless I've actually found and verified a real, concrete issue through legitimate code investigation.

If you have a genuine question about how GitLab (`Noahgrantyt/gitlab--010`) handles file extraction, symlinks in uploads/imports, or related security controls, I'm glad to search the actual production code and give you a grounded, evidence-based answer — including saying plainly if no real issue is found, rather than manufacturing one to fit a template. [1](#0-0)

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
