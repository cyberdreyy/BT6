# Q2406: identity provider failure fails open in oidc.NewOIDCAuthenticator

## Question
If the external identity backend behind `NewOIDCAuthenticator` is unreachable, does the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled fall back to a permissive path that authenticates an unauthenticated HTTP client that can reach the node API port or maps them to a default role?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `NewOIDCAuthenticator`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: group claim values (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger the failure while submitting `group claim values`.
- Invariant to test: backend failure must fail closed with no role assignment
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test injecting backend errors and asserting a 401 with no session
