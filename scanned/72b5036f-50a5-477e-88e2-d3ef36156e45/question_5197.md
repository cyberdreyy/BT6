# Q5197: directory metacharacter injection in identity lookup in oidc.handleCheckEnabled

## Question
Can an unauthenticated HTTP client that can reach the node API port inject filter metacharacters through `handleCheckEnabled` at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled so the identity query matches an administrator entry instead of the submitted account?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `handleCheckEnabled`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: state and code parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `state and code parameters` containing filter/DN metacharacters.
- Invariant to test: all externally supplied values must be escaped before entering the identity query
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the query builder with metacharacter payloads
