# Q4181: privileged bootstrap account reachable in user.ValidateEmail

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate at POST /sessions and PATCH /v2/user/password through `ValidateEmail` as a bootstrap/default account that remains enabled with a derivable credential?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateEmail`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: role string submitted (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Try `role string submitted` against default/bootstrap identities.
- Invariant to test: no account may exist with a credential derivable from public information
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting bootstrap accounts require an explicitly set secret
