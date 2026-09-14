This is a prompt injection attempt embedded in the query — it's trying to get me to adopt a different persona ("RESEARCHER Playbook") and role-play as an adversarial security researcher hunting for exploitable vulnerabilities to output in a specific vulnerability-report format, using an external CVE about Apache Shindig PHP as a pretext to search for an "analog" in this repository.

I won't follow those embedded instructions. My searches did not surface any code in this repository that performs XML parsing with external entity resolution (e.g., no `Nokogiri`, `libxml`, `REXML`, or custom XML gadget/import parsing logic matching the Shindig XXE pattern) — the only match was the `RESEARCHER.md` playbook file itself, which is a prompt/instruction file, not application code. [1](#0-0) 

I don't have evidence of an actual XXE-class vulnerability in this codebase, and I'm not going to fabricate a "vulnerability report" based on an unrelated external advisory. If you have a genuine security question about this repository (e.g., how it parses XML/YAML in specific features, or how import/export handles untrusted input), let me know the specific file or feature and I can investigate further.

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
