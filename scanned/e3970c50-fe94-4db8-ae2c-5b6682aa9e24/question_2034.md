# Q2034: guardRunnerCommand lower-trust repo state survives into protected jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `guardRunnerCommand` preserve lower-trust checkout or config residue until a protected or unrelated ref consumes it?

## Target
- File/function: shells/abstract.go: guardRunnerCommand
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, repeated jobs across protected and unprotected refs
- Exploit idea: leave repo state behind and rely on reuse across trust boundaries
- Invariant to test: source checkout and config state must stay bound to the current ref and protection level
- Expected Immunefi impact: protected-ref escalation via checkout-state reuse
- Fast validation: seed lower-trust repo state and verify protected refs rebuild or isolate state
