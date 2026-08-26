# Q0730: WebAuthn assertion not bound to the challenge in oidc.NewOIDCAuthenticator

## Question
Can an unauthenticated HTTP client that can reach the node API port replay or forge the assertion validated by `NewOIDCAuthenticator` at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled because the challenge, origin or user handle is not bound to the session being authenticated?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `NewOIDCAuthenticator`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: redirect/callback URL (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `redirect/callback URL` captured from another login or another user.
- Invariant to test: an assertion must match the freshly issued challenge, RP origin and the authenticating user handle
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test replaying an assertion across sessions and users
