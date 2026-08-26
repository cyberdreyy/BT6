# Q2401: identity provider failure fails open in orm.NewORM

## Question
If the external identity backend behind `NewORM` is unreachable, does POST /sessions, API-token auth headers and session cookie lookup fall back to a permissive path that authenticates an unauthenticated HTTP client that can reach the node API port or maps them to a default role?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `NewORM`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: access key/secret pair (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger the failure while submitting `access key/secret pair`.
- Invariant to test: backend failure must fail closed with no role assignment
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test injecting backend errors and asserting a 401 with no session
