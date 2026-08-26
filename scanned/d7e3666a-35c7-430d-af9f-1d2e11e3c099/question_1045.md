# Q1045: group-to-role mapping too permissive in oidc.NewOIDCAuthenticator

## Question
Does the group-to-role mapping performed by `NewOIDCAuthenticator` at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled grant an elevated role on a partial, case-insensitive or substring match, letting an unauthenticated HTTP client that can reach the node API port in a low-privilege group obtain admin?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `NewOIDCAuthenticator`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: redirect/callback URL (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Authenticate with `redirect/callback URL` from a group whose name embeds the privileged group name.
- Invariant to test: group mapping must be an exact match against the configured DN/claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test mapping crafted group names to roles
