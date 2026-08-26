# Q0962: directory metacharacter injection in identity lookup in orm.NewORM

## Question
Can an unauthenticated HTTP client that can reach the node API port inject filter metacharacters through `NewORM` at POST /sessions, API-token auth headers and session cookie lookup so the identity query matches an administrator entry instead of the submitted account?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `NewORM`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: email casing/whitespace (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `email casing/whitespace` containing filter/DN metacharacters.
- Invariant to test: all externally supplied values must be escaped before entering the identity query
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the query builder with metacharacter payloads
