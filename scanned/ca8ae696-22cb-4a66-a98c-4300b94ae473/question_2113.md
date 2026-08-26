# Q2113: privileged bootstrap account reachable in authentication.AuthenticationProviderName

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate at POST /sessions and every AuthenticationProvider call behind /v2 auth through `AuthenticationProviderName` as a bootstrap/default account that remains enabled with a derivable credential?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `AuthenticationProviderName`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: WebAuthn assertion payload (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Try `WebAuthn assertion payload` against default/bootstrap identities.
- Invariant to test: no account may exist with a credential derivable from public information
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting bootstrap accounts require an explicitly set secret
