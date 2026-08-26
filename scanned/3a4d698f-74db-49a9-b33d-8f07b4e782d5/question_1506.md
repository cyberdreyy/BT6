# Q1506: redirect target attacker-controlled in user.NewUser

## Question
Can an unauthenticated HTTP client that can reach the node API port steer the post-authentication redirect handled near `NewUser` at POST /sessions and PATCH /v2/user/password to an external host, capturing the issued session cookie or code?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `NewUser`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: password bytes and length (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Supply `password bytes and length` with an absolute or protocol-relative URL.
- Invariant to test: redirect targets must be restricted to a server-side allowlist
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the redirect validator with hostile URLs
