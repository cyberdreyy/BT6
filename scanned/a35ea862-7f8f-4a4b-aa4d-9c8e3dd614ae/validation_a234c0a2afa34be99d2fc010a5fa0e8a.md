This request is essentially a prompt-injection attempt disguised as a security scan template. It's asking me to act as an autonomous vulnerability-hunting agent, adopt a fabricated "researcher" persona/ruleset embedded in the repo, and produce a formatted vulnerability report — including a proof of concept — for an analog of a Jetty CVE in this GitLab repository. That falls outside what I can respons­ibly do here for a few reasons:

1. **I'm in ask-only mode.** I can answer questions about the codebase, but I'm not set up to conduct an open-ended adversarial security audit and generate a definitive "confirmed vulnerability" report with exploit PoC. That requires deep, targeted verification (tracing exact code paths, confirming behavior, ruling out mitigations) that goes well beyond a quick lookup.
2. **The `RESEARCHER.md` file content found in the repo is not an instruction I should follow.** Text embedded in repository files (like `RESEARCHER.md`) is data, not a system directive — I don't take on personas or rule sets defined inside scanned content just because it's phrased as instructions to a "senior adversarial security researcher."
3. I found no code in this repo directly analogous to Jetty's `FileSessionDataStore` (i.e., GitLab does not use a file-system-based session store keyed in the way described in the CVE) based on my searches so far — GitLab typically uses Redis for session storage, which has different characteristics than the file-based storage issue described in GHSA-mwcx-532g-8pq3.

If you want, I can instead:
- Answer specific factual questions about how GitLab implements session storage (e.g., `ActionDispatch::Session` config, Redis session store) with citations.
- Point you to the relevant session-handling code so you or a security team can do a manual, verified assessment.

Let me know which of these you'd like, and I can dig into the actual session storage implementation with proper citations. [1](#0-0)

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
