# Q1774: listing renders objects across owners in external_initiators.NewExternalInitiatorResource

## Question
Does the collection built by `NewExternalInitiatorResource` at the JSON:API response of /v2/external_initiators render objects outside an authenticated node user holding only the 'view' role's entitlement together with their sensitive attributes?

## Target
- File/function: [core/web/presenters/external_initiators.go](core/web/presenters/external_initiators.go) -> `NewExternalInitiatorResource`
- Entrypoint: the JSON:API response of /v2/external_initiators
- Attacker controls: the initiator requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `initiator requested` as a low-role user.
- Invariant to test: collections must be filtered before rendering
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing collection contents per role
