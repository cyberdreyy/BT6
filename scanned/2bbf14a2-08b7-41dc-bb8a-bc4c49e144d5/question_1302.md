# Q1302: Resolve special-character variable parsing confusion

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `Resolve` mis-handle special-character variable names or references so the shell reads a different variable or evaluates unexpected syntax?

## Target
- File/function: common/secrets.go: Resolve
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, special-character variable names and references
- Exploit idea: trigger parser differences between generated references and intended variable names
- Invariant to test: generated variable references must bind only to the intended variable
- Expected Immunefi impact: secret exposure or command injection
- Fast validation: use special-character variable names and verify the generated script references them safely
