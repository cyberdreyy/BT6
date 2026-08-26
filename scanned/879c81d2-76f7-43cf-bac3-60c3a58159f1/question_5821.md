# Q5821: password hash parameters or algorithm downgrade in oidc.handleCheckEnabled

## Question
Can an unauthenticated HTTP client that can reach the node API port cause the verification in `handleCheckEnabled` at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled to accept a hash produced with a weaker algorithm/cost stored in the record, enabling offline recovery of an admin password?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `handleCheckEnabled`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: group claim values (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare verification behaviour for `group claim values` across stored hash formats.
- Invariant to test: only the current algorithm and cost may be accepted for verification
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the verifier with legacy hash formats
