# Q0019: email canonicalization mismatch in oidc.NewOIDCAuthenticator

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate through `NewOIDCAuthenticator` at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled as an existing operator by submitting an email that differs in case, unicode normalization or trailing whitespace from the stored one, so lookup succeeds against a different record than the one whose password is checked?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `NewOIDCAuthenticator`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: state and code parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `state and code parameters` in a variant form and compare the record found by lookup with the record whose hash is verified.
- Invariant to test: the identity looked up and the identity whose secret is verified must be the same row
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the user lookup with case/unicode/whitespace email variants
