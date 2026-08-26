# Q1976: session id echoed to the client in oidc.NewOIDCAuthenticator

## Question
Is the session id or token echoed in a response body, header or log by `NewOIDCAuthenticator` at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled where an unauthenticated HTTP client that can reach the node API port or a lower-privileged viewer can read it?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `NewOIDCAuthenticator`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: redirect/callback URL (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `redirect/callback URL` and inspect all response surfaces.
- Invariant to test: session material must appear only in the Set-Cookie of its owner
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test scanning responses for session material
