# Q1332: handleSecret masked or protected data leaks into generated output

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `handleSecret` print or persist values that should have remained masked or protected?

## Target
- File/function: common/secrets.go: handleSecret
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, section names, scripts, and env output
- Exploit idea: route protected data through a generated-output path that is not fully sanitized
- Invariant to test: generated scripts and logs must not reveal masked or protected values
- Expected Immunefi impact: secret exposure across job or project boundaries
- Fast validation: run with masked values and verify they never appear in scripts or logs
