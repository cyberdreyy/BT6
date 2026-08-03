# Q1320: Resolve repeated setup reuses stale temp or config state

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `Resolve` reuse stale temp files, git config, or generated shell state from an earlier logical operation?

## Target
- File/function: common/secrets.go: Resolve
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, retries, repeated stages, and stale temp state
- Exploit idea: have later setup steps trust old state instead of rebuilding from current inputs
- Invariant to test: each logical setup run must get fresh state bound to the current job inputs
- Expected Immunefi impact: cross-job confusion or trusted-runtime reuse
- Fast validation: repeat setup flows and verify stale temp/config state is never reused
