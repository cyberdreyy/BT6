# Q1304: Resolve env or dotenv path escapes temp roots

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `Resolve` read from or write to an env file outside the runner temp and generated-config roots?

## Target
- File/function: common/secrets.go: Resolve
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, env-file paths and path aliases
- Exploit idea: select env-file locations that resolve outside assigned temp roots
- Invariant to test: env and dotenv files must remain inside the runner temp and generated-config roots
- Expected Immunefi impact: trusted-runtime override or secret-bearing file overwrite
- Fast validation: supply escaping env-file paths and verify the operation is rejected or confined
