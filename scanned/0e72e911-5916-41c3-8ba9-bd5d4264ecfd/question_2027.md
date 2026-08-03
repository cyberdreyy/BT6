# Q2027: guardRunnerCommand external or global git config persists across jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `guardRunnerCommand` place or reuse git config state that survives into a later job with a stronger trust boundary?

## Target
- File/function: shells/abstract.go: guardRunnerCommand
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, git config includes and temp config files
- Exploit idea: leave git config state on disk where later jobs inherit it
- Invariant to test: git config state must be unique per job and cleaned before trust boundaries change
- Expected Immunefi impact: protected-ref escalation or credential misuse
- Fast validation: run sequential jobs and verify no git config state persists across them
