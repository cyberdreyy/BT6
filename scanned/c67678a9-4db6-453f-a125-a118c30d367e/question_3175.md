# Q3175: MFA store cookie forgeable in oidc.generateState

## Question
Is the WebAuthn session-store cookie handled around `generateState` unauthenticated or unsigned, letting an unauthenticated HTTP client that can reach the node API port craft one at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled to complete an MFA step for another user?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `generateState`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: group claim values (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `group claim values` with attacker-chosen contents.
- Invariant to test: the MFA session store must be server-side or authenticated
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting a tampered store cookie is rejected
