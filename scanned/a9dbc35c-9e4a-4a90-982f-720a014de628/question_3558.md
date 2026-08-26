# Q3558: token claims trusted without verification in oidc.generateState

## Question
Does the identity token processed by `generateState` at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled get accepted with unverified signature, issuer, audience or expiry, letting an unauthenticated HTTP client that can reach the node API port present a self-issued token and become an admin?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `generateState`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: redirect/callback URL (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `redirect/callback URL` signed by an attacker key or with alg/kid manipulated.
- Invariant to test: identity tokens must be verified against the configured issuer keys, audience and expiry
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test presenting self-signed and expired tokens
