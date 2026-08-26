# Q3231: directory metacharacter injection in identity lookup in session.GenerateAuthToken

## Question
Can an unauthenticated HTTP client that can reach the node API port inject filter metacharacters through `GenerateAuthToken` at POST /sessions (session creation) and API-token authentication so the identity query matches an administrator entry instead of the submitted account?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `GenerateAuthToken`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: supplied access key and secret (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `supplied access key and secret` containing filter/DN metacharacters.
- Invariant to test: all externally supplied values must be escaped before entering the identity query
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the query builder with metacharacter payloads
