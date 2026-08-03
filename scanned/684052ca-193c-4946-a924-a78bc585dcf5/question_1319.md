# Q1319: Resolve path normalization mismatch bypasses checks

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `Resolve` validate one path spelling but operate on another equivalent path after normalization differences?

## Target
- File/function: common/secrets.go: Resolve
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, slash, backslash, case, or drive aliases
- Exploit idea: pass validation on one representation and execute on another equivalent path
- Invariant to test: validation and final path use must agree on one canonical in-root path
- Expected Immunefi impact: cross-job tampering or stronger-context file overwrite
- Fast validation: exercise path aliases and verify consistent canonicalization
