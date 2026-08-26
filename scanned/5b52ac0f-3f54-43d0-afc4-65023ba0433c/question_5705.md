# Q5705: login race creates duplicate identity in orm.FindUserByAPIToken

## Question
Can concurrent requests to POST /sessions, API-token auth headers and session cookie lookup racing inside `FindUserByAPIToken` create two sessions or two user rows for one identity, so an unauthenticated HTTP client that can reach the node API port keeps a session that the operator cannot see or revoke?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `FindUserByAPIToken`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: password bytes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `password bytes`.
- Invariant to test: session and user creation must be serialized and idempotent per identity
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: concurrent test asserting a single row/session results
