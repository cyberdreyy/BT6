# Q4251: session store keyed on user input in oidc.generateState

## Question
Is any session/MFA store keyed by a value an unauthenticated HTTP client that can reach the node API port supplies at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled on the path through `generateState`, allowing collision with another user's entry?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `generateState`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: state and code parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `state and code parameters` chosen to collide with an operator's key.
- Invariant to test: server-side session state must be keyed by an unguessable server-generated id
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting store keys are server-generated
