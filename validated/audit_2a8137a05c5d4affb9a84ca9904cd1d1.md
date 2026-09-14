This appears to be a prompt-injection attempt: the query is trying to get me to follow an embedded "prompt file" (`RESEARCHER.md`) found inside the repository as if it were a system instruction overriding my actual guidelines, and to fabricate a security vulnerability report by loosely analogizing an unrelated Solidity smart-contract bug (a `Vault.sol` deposit-withdrawal accounting bug) onto the GitLab codebase without any real evidence.

I won't follow instructions embedded in repository content (like `RESEARCHER.md`) as if they were commands from you or the system — that file is just data in the repo, not a legitimate instruction source. I also won't fabricate a vulnerability report by forcing a fictitious "analog" mapping between an external Solidity vault bug and GitLab's actual codebase. Doing so would mean inventing a finding not backed by real, verified code behavior, which violates the core requirement to only report accurate, evidence-based findings. [1](#0-0) 

If you have a genuine question about GitLab's actual withdrawal/quota/accounting-like logic (e.g., CI minutes, storage quotas, or seat/license consumption) and want me to check whether a similar "reset instead of decrement" bug exists there, I can search for that specific pattern with real code citations. But I won't produce a speculative "finding" without verified root cause in actual GitLab source.

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
