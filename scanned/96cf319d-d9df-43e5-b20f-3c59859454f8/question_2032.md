# Q2032: guardRunnerCommand masked or protected data leaks into generated output

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `guardRunnerCommand` print or persist values that should have remained masked or protected?

## Target
- File/function: shells/abstract.go: guardRunnerCommand
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, section names, scripts, and env output
- Exploit idea: route protected data through a generated-output path that is not fully sanitized
- Invariant to test: generated scripts and logs must not reveal masked or protected values
- Expected Immunefi impact: secret exposure across job or project boundaries
- Fast validation: run with masked values and verify they never appear in scripts or logs
