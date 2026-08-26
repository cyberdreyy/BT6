# Q3166: MFA store cookie forgeable in authentication.BasicAdminUsersORM

## Question
Is the WebAuthn session-store cookie handled around `BasicAdminUsersORM` unauthenticated or unsigned, letting an unauthenticated HTTP client that can reach the node API port craft one at POST /sessions and every AuthenticationProvider call behind /v2 auth to complete an MFA step for another user?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `BasicAdminUsersORM`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: WebAuthn assertion payload (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `WebAuthn assertion payload` with attacker-chosen contents.
- Invariant to test: the MFA session store must be server-side or authenticated
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting a tampered store cookie is rejected
