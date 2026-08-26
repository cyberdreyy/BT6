# Q0809: WebAuthn registration bound to the wrong user in oidc.NewOIDCAuthenticator

## Question
Can an unauthenticated HTTP client that can reach the node API port register a credential through `NewOIDCAuthenticator` at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled that becomes attached to another user's account, giving permanent MFA-satisfying access?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `NewOIDCAuthenticator`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: ID token claims presented (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `ID token claims presented` with a user handle or session store cookie referring to a different account.
- Invariant to test: the registered credential must attach to the authenticated session's user only
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the stored credential's user id equals the session user
