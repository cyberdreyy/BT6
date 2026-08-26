# Q4428: identity provider failure fails open in authentication.BasicAdminUsersORM

## Question
If the external identity backend behind `BasicAdminUsersORM` is unreachable, does POST /sessions and every AuthenticationProvider call behind /v2 auth fall back to a permissive path that authenticates an unauthenticated HTTP client that can reach the node API port or maps them to a default role?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `BasicAdminUsersORM`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: WebAuthn assertion payload (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger the failure while submitting `WebAuthn assertion payload`.
- Invariant to test: backend failure must fail closed with no role assignment
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test injecting backend errors and asserting a 401 with no session
