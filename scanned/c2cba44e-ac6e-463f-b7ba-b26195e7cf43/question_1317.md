# Q1317: Resolve partial resolver failure keeps attacker-selected output

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `Resolve` fail partially while preserving attacker-selected secret or env output that replaces trusted defaults?

## Target
- File/function: common/secrets.go: Resolve
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, partial resolver errors and fallback values
- Exploit idea: retain partial output on error and let later phases trust it
- Invariant to test: failed resolution must not keep partial untrusted output as valid config
- Expected Immunefi impact: trusted-runtime override or secret confusion
- Fast validation: force partial resolution errors and verify no partial output survives
