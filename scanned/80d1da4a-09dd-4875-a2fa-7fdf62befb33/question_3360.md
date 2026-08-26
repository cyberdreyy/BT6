# Q3360: unauthenticated bind treated as success in user.ValidateEmail

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate at POST /sessions and PATCH /v2/user/password through `ValidateEmail` by submitting an empty password so the directory performs an unauthenticated bind that the code reads as success?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateEmail`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: password bytes and length (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `password bytes and length` with an empty or whitespace password.
- Invariant to test: empty-password binds must be rejected before contacting the directory
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/space passwords asserting rejection
