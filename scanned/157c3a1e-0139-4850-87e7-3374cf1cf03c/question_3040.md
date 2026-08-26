# Q3040: WebAuthn assertion not bound to the challenge in user.ValidateEmail

## Question
Can an unauthenticated HTTP client that can reach the node API port replay or forge the assertion validated by `ValidateEmail` at POST /sessions and PATCH /v2/user/password because the challenge, origin or user handle is not bound to the session being authenticated?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateEmail`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: role string submitted (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `role string submitted` captured from another login or another user.
- Invariant to test: an assertion must match the freshly issued challenge, RP origin and the authenticating user handle
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test replaying an assertion across sessions and users
