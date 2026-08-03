# Q1336: handleSecret host normalization misroutes authentication

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `handleSecret` treat two remote hosts or paths as equivalent for auth purposes even though they are distinct security principals?

## Target
- File/function: common/secrets.go: handleSecret
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, visually similar hosts, schemes, or paths
- Exploit idea: make auth scope broader than the final effective remote
- Invariant to test: auth scope must remain attached to the exact final remote principal
- Expected Immunefi impact: credential disclosure or unauthorized repo access
- Fast validation: use equivalent-looking hosts and verify auth does not cross principals
