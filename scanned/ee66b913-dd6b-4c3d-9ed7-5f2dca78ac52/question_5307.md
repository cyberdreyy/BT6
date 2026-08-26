# Q5307: unauthenticated bind treated as success in orm.FindUserByAPIToken

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate at POST /sessions, API-token auth headers and session cookie lookup through `FindUserByAPIToken` by submitting an empty password so the directory performs an unauthenticated bind that the code reads as success?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `FindUserByAPIToken`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: session id in the cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `session id in the cookie` with an empty or whitespace password.
- Invariant to test: empty-password binds must be rejected before contacting the directory
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/space passwords asserting rejection
