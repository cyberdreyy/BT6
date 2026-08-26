# Q2464: email canonicalization mismatch in user.ValidateEmail

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate through `ValidateEmail` at POST /sessions and PATCH /v2/user/password as an existing operator by submitting an email that differs in case, unicode normalization or trailing whitespace from the stored one, so lookup succeeds against a different record than the one whose password is checked?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateEmail`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: role string submitted (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `role string submitted` in a variant form and compare the record found by lookup with the record whose hash is verified.
- Invariant to test: the identity looked up and the identity whose secret is verified must be the same row
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the user lookup with case/unicode/whitespace email variants
