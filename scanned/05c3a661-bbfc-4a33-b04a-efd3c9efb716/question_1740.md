# Q1740: user enumeration then targeted attack in user.NewUser

## Question
Do responses from `NewUser` at POST /sessions and PATCH /v2/user/password distinguish unknown accounts from wrong passwords precisely enough for an unauthenticated HTTP client that can reach the node API port to enumerate operator accounts before credential attacks?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `NewUser`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: password bytes and length (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare status/body/timing for `password bytes and length` across known and unknown accounts.
- Invariant to test: authentication failures must be uniform in content and timing
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test comparing responses for known/unknown accounts
