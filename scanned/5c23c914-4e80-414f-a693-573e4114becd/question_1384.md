# Q1384: identifier reveals sensitive identity in external_initiators.NewExternalInitiatorResource

## Question
Does the identifier or metadata rendered by `NewExternalInitiatorResource` at the JSON:API response of /v2/external_initiators reveal key identities, addresses or credential fingerprints that let an authenticated node user holding only the 'view' role target key theft or fund movement?

## Target
- File/function: [core/web/presenters/external_initiators.go](core/web/presenters/external_initiators.go) -> `NewExternalInitiatorResource`
- Entrypoint: the JSON:API response of /v2/external_initiators
- Attacker controls: create vs index route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `create vs index route selection` at the lowest role.
- Invariant to test: identity metadata must be limited to what the caller's role needs
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing rendered identifiers per role
