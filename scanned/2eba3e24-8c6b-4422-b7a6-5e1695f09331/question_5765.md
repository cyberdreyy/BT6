# Q5765: user enumeration then targeted attack in oidc.handleCheckEnabled

## Question
Do responses from `handleCheckEnabled` at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled distinguish unknown accounts from wrong passwords precisely enough for an unauthenticated HTTP client that can reach the node API port to enumerate operator accounts before credential attacks?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `handleCheckEnabled`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: ID token claims presented (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare status/body/timing for `ID token claims presented` across known and unknown accounts.
- Invariant to test: authentication failures must be uniform in content and timing
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test comparing responses for known/unknown accounts
