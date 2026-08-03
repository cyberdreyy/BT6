# Q1330: handleSecret checkout or include path escapes the workspace

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `handleSecret` resolve checkout, include, or config paths outside the runner temp and generated-config roots?

## Target
- File/function: common/secrets.go: handleSecret
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, checkout and include paths
- Exploit idea: drive path resolution onto external or sibling runner paths
- Invariant to test: checkout and include paths must remain inside the runner temp and generated-config roots
- Expected Immunefi impact: cross-job tampering or helper-state overwrite
- Fast validation: supply escaping checkout/include paths and verify confinement
