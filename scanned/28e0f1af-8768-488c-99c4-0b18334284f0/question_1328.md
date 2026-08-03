# Q1328: handleSecret credential helper binds tokens to the wrong host

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `handleSecret` prepare credentials for one host or remote and then send them to another after host or URL normalization changes?

## Target
- File/function: common/secrets.go: handleSecret
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, remote URLs, insteadOf rules, and host aliases
- Exploit idea: change the effective remote after credentials were prepared
- Invariant to test: credential binding must remain attached to the final intended host
- Expected Immunefi impact: token disclosure or unauthorized repo access
- Fast validation: use rewritten remotes and verify credentials only go to the final approved host
