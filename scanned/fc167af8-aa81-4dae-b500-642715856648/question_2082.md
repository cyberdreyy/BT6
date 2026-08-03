# Q2082: guardGetSourcesScriptHooks special-character variable parsing confusion

## Question
Can an unprivileged GitLab user or pipeline author enter through job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables and make `guardGetSourcesScriptHooks` mis-handle special-character variable names or references so the shell reads a different variable or evaluates unexpected syntax?

## Target
- File/function: shells/abstract.go: guardGetSourcesScriptHooks
- Entrypoint: job setup using attacker-controlled refs, repo files, submodule state, dotenv input, or CI variables
- Attacker controls: refs, repo URLs, submodule URLs, checkout paths, env files, git config fragments, and repo-controlled files, special-character variable names and references
- Exploit idea: trigger parser differences between generated references and intended variable names
- Invariant to test: generated variable references must bind only to the intended variable
- Expected Immunefi impact: secret exposure or command injection
- Fast validation: use special-character variable names and verify the generated script references them safely
