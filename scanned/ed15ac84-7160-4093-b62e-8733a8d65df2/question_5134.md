# Q5134: MFA store cookie forgeable in user.ValidateAndHashPassword

## Question
Is the WebAuthn session-store cookie handled around `ValidateAndHashPassword` unauthenticated or unsigned, letting an unauthenticated HTTP client that can reach the node API port craft one at POST /sessions and PATCH /v2/user/password to complete an MFA step for another user?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateAndHashPassword`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: email string (unicode, case, whitespace) (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `email string (unicode, case, whitespace)` with attacker-chosen contents.
- Invariant to test: the MFA session store must be server-side or authenticated
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting a tampered store cookie is rejected
