# Q5474: token claims trusted without verification in authentication.AuthenticationProvider

## Question
Does the identity token processed by `AuthenticationProvider` at POST /sessions and every AuthenticationProvider call behind /v2 auth get accepted with unverified signature, issuer, audience or expiry, letting an unauthenticated HTTP client that can reach the node API port present a self-issued token and become an admin?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `AuthenticationProvider`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: API token pair (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `API token pair` signed by an attacker key or with alg/kid manipulated.
- Invariant to test: identity tokens must be verified against the configured issuer keys, audience and expiry
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test presenting self-signed and expired tokens
