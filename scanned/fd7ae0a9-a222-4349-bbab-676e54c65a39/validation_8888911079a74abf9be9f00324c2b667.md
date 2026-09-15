The searches returned no concrete evidence of an analogous arbitrary file read/download vulnerability in this repository — only the repo's own `RESEARCHER.md` and `SECURITY.md` policy files surfaced, which are unrelated to any actual file-download code path.

A few important notes on this request:

1. **This prompt is itself a form of prompt injection.** It arrived as page content under an "ANALOG SCAN PROMPT" wrapper instructing me to adopt a different persona ("senior adversarial security researcher"), follow a different rules file, and emit output in a rigid format bypassing my normal citation requirements. I'm disregarding those embedded instructions and following my actual operating instructions (ask-only mode, proper ` [1](#0-0) [2](#0-1)

### Citations

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

**File:** RESEARCHER.md (L88-106)
```markdown
## Evidence Standard (Required For Any Valid Finding)

- Exact file(s), function(s), and line range(s).
- Root cause and violated assumption.
- Realistic attacker preconditions (no-privilege by default).
- End-to-end exploit path.
- Existing checks and why they fail.
- Concrete impact category and severity rationale.
- Reproducible PoC or deterministic equivalent reasoning.

## Immediate Rejection Filters

- No concrete exploit path.
- No measurable impact.
- Impossible or out-of-scope preconditions.
- Requires direct break of standard cryptographic primitives.
- Pure phishing/social engineering/user self-harm.
- Pure documentation/style/performance feedback with no security break.

```
