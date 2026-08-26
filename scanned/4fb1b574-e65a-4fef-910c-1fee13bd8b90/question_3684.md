# Q3684: redirect target attacker-controlled in oidc.generateState

## Question
Can an unauthenticated HTTP client that can reach the node API port steer the post-authentication redirect handled near `generateState` at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled to an external host, capturing the issued session cookie or code?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `generateState`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: group claim values (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Supply `group claim values` with an absolute or protocol-relative URL.
- Invariant to test: redirect targets must be restricted to a server-side allowlist
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the redirect validator with hostile URLs
