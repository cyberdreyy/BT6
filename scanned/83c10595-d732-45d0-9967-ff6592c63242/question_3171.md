# Q3171: MFA store cookie forgeable in reaper.Work

## Question
Is the WebAuthn session-store cookie handled around `Work` unauthenticated or unsigned, letting an authenticated node user holding only the 'view' role craft one at any authenticated /v2 request made after logout, password change or role change to complete an MFA step for another user?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `Work`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: repeated reuse of an old session id (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `repeated reuse of an old session id` with attacker-chosen contents.
- Invariant to test: the MFA session store must be server-side or authenticated
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting a tampered store cookie is rejected
