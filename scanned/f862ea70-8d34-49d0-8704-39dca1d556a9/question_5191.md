# Q5191: directory metacharacter injection in identity lookup in user.ValidateAndHashPassword

## Question
Can an unauthenticated HTTP client that can reach the node API port inject filter metacharacters through `ValidateAndHashPassword` at POST /sessions and PATCH /v2/user/password so the identity query matches an administrator entry instead of the submitted account?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateAndHashPassword`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: password bytes and length (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `password bytes and length` containing filter/DN metacharacters.
- Invariant to test: all externally supplied values must be escaped before entering the identity query
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the query builder with metacharacter payloads
