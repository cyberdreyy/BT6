# Q2122: privileged bootstrap account reachable in oidc.NewOIDCAuthenticator

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled through `NewOIDCAuthenticator` as a bootstrap/default account that remains enabled with a derivable credential?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `NewOIDCAuthenticator`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: group claim values (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Try `group claim values` against default/bootstrap identities.
- Invariant to test: no account may exist with a credential derivable from public information
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting bootstrap accounts require an explicitly set secret
