# Q3864: user enumeration then targeted attack in authentication.BasicAdminUsersORM

## Question
Do responses from `BasicAdminUsersORM` at POST /sessions and every AuthenticationProvider call behind /v2 auth distinguish unknown accounts from wrong passwords precisely enough for an unauthenticated HTTP client that can reach the node API port to enumerate operator accounts before credential attacks?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `BasicAdminUsersORM`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: session id presented (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare status/body/timing for `session id presented` across known and unknown accounts.
- Invariant to test: authentication failures must be uniform in content and timing
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test comparing responses for known/unknown accounts
