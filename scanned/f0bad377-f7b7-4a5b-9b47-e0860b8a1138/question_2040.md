# Q2040: guardRunnerCommand repeated setup reuses stale temp or config state

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `guardRunnerCommand` reuse stale temp files, git config, or generated shell state from an earlier logical operation?

## Target
- File/function: shells/abstract.go: guardRunnerCommand
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, retries, repeated stages, and stale temp state
- Exploit idea: have later setup steps trust old state instead of rebuilding from current inputs
- Invariant to test: each logical setup run must get fresh state bound to the current job inputs
- Expected Immunefi impact: cross-job confusion or trusted-runtime reuse
- Fast validation: repeat setup flows and verify stale temp/config state is never reused
