# Q5311: unauthenticated bind treated as success in oidc.handleCheckEnabled

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled through `handleCheckEnabled` by submitting an empty password so the directory performs an unauthenticated bind that the code reads as success?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `handleCheckEnabled`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: ID token claims presented (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `ID token claims presented` with an empty or whitespace password.
- Invariant to test: empty-password binds must be rejected before contacting the directory
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/space passwords asserting rejection
