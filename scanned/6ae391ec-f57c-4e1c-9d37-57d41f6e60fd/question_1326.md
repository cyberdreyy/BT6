# Q1326: handleSecret cleanup path escapes the assigned roots

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `handleSecret` derive a cleanup target that escapes the assigned root and removes or rewrites unrelated runner state?

## Target
- File/function: common/secrets.go: handleSecret
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, cleanup paths, slash variants, and relative segments
- Exploit idea: turn cleanup into a cross-boundary file operation
- Invariant to test: cleanup must remain confined to current job-owned paths
- Expected Immunefi impact: cross-job state tampering
- Fast validation: drive cleanup with escaping paths and verify only job-owned files are touched
