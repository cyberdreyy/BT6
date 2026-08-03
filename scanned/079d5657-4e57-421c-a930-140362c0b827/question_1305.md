# Q1305: Resolve temp file or script path collides with trusted files

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `Resolve` choose a temp or script path that collides with an existing trusted file consumed by later stages?

## Target
- File/function: common/secrets.go: Resolve
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, colliding temp names and path aliases
- Exploit idea: force temp naming to reuse an attacker-controlled or already-trusted path
- Invariant to test: generated temp and script files must not collide with existing trusted files
- Expected Immunefi impact: later-stage hijack or secret exposure
- Fast validation: pre-place colliding files and verify generated paths remain unique and confined
