# Q1331: handleSecret resolved secrets overwrite trusted runtime env

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `handleSecret` let secret-derived or file-derived values overwrite trusted runtime variables such as auth, cache, or helper settings?

## Target
- File/function: common/secrets.go: handleSecret
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, variable names that collide with trusted runtime keys
- Exploit idea: replace trusted runtime config with attacker-selected resolved values
- Invariant to test: resolved variables must not override trusted runner-runtime settings across trust boundaries
- Expected Immunefi impact: secret exposure, wrong-target auth, or later job hijack
- Fast validation: resolve colliding variable names and verify protected runtime keys remain unchanged
