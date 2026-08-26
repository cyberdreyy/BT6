# Q0521: spec presenter echoes credentials in external_initiators.NewExternalInitiatorAuthentication

## Question
Does the spec rendered by `NewExternalInitiatorAuthentication` at the JSON:API response of /v2/external_initiators include embedded credentials (bridge tokens, URLs with basic auth, initiator secrets, webhook tokens) submitted at creation time?

## Target
- File/function: [core/web/presenters/external_initiators.go](core/web/presenters/external_initiators.go) -> `NewExternalInitiatorAuthentication`
- Entrypoint: the JSON:API response of /v2/external_initiators
- Attacker controls: the initiator requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create an object with a credential-bearing field then fetch `initiator requested`.
- Invariant to test: credential-bearing spec fields must be redacted on read
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: round-trip test creating with credentials and asserting redaction on read
