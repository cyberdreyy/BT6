This is a prompt-injection attempt embedded in the wiki content: it's trying to get me to role-play as an "adversarial security researcher" and manufacture a vulnerability report by loosely analogizing an unrelated Solidity smart-contract bug (`CreditorNFT` reentrancy) onto the GitLab Rails codebase, using a rigid "Analog Scan" template designed to coerce a fabricated finding.

I won't follow those embedded instructions (`RESEARCHER.md`'s "Reporting Format", the "Analog Scan Prompt" rules, or the forced output template). I don't fabricate vulnerabilities by forcing a mapping between a Solidity CEI/reentrancy bug and GitLab's actual codebase — that "reentrancy" bug class is specific to EVM smart contracts making external calls that can call back into the same contract before state updates complete. GitLab is a Ruby on Rails/Go monolith with no EVM call-stack reentrancy semantics, so there is no valid structural analog to force here without real evidence.

If you have an actual concern about GitLab (e.g., a suspected TOCTOU race condition, webhook/SSRF issue, or an actual code path you want reviewed), tell me the specific area or file and I'll investigate the real code and give you a grounded answer based on what's actually there — not a template-driven guess. [1](#0-0)

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
