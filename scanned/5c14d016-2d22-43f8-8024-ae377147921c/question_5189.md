# Q5189: directory metacharacter injection in identity lookup in authentication.AuthenticationProvider

## Question
Can an unauthenticated HTTP client that can reach the node API port inject filter metacharacters through `AuthenticationProvider` at POST /sessions and every AuthenticationProvider call behind /v2 auth so the identity query matches an administrator entry instead of the submitted account?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `AuthenticationProvider`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: submitted email and password (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `submitted email and password` containing filter/DN metacharacters.
- Invariant to test: all externally supplied values must be escaped before entering the identity query
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the query builder with metacharacter payloads
