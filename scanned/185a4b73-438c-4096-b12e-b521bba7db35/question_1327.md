# Q1327: handleSecret external or global git config persists across jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `handleSecret` place or reuse git config state that survives into a later job with a stronger trust boundary?

## Target
- File/function: common/secrets.go: handleSecret
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, git config includes and temp config files
- Exploit idea: leave git config state on disk where later jobs inherit it
- Invariant to test: git config state must be unique per job and cleaned before trust boundaries change
- Expected Immunefi impact: protected-ref escalation or credential misuse
- Fast validation: run sequential jobs and verify no git config state persists across them
