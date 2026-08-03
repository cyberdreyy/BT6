# Q1314: Resolve lower-trust repo state survives into protected jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job and make `Resolve` preserve lower-trust checkout or config residue until a protected or unrelated ref consumes it?

## Target
- File/function: common/secrets.go: Resolve
- Entrypoint: secret/env resolution from `.gitlab-ci.yml`, CI variables, and external secret references in an unprivileged job
- Attacker controls: secret names, resolver inputs, variable names, dotenv content, and downstream script references, repeated jobs across protected and unprotected refs
- Exploit idea: leave repo state behind and rely on reuse across trust boundaries
- Invariant to test: source checkout and config state must stay bound to the current ref and protection level
- Expected Immunefi impact: protected-ref escalation via checkout-state reuse
- Fast validation: seed lower-trust repo state and verify protected refs rebuild or isolate state
