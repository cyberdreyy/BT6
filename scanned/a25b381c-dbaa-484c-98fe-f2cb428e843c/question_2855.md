# Q2855: password change without old-password proof in oidc.generateState

## Question
Can an unauthenticated HTTP client that can reach the node API port change the password (or set a new one) through the path reaching `generateState` at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled without a verified old password or with the check applied to the wrong account?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `generateState`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: ID token claims presented (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `ID token claims presented` naming another account or omitting the old-password field.
- Invariant to test: password change must verify the old password of exactly the authenticated account
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test changing another user's password from a view-role session
