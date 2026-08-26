# Q1072: struct embedding pulls in secret fields in external_initiators.NewExternalInitiatorResource

## Question
Does `NewExternalInitiatorResource` embed a domain struct so newly added secret fields are serialized automatically at the JSON:API response of /v2/external_initiators without anyone reviewing the response shape?

## Target
- File/function: [core/web/presenters/external_initiators.go](core/web/presenters/external_initiators.go) -> `NewExternalInitiatorResource`
- Entrypoint: the JSON:API response of /v2/external_initiators
- Attacker controls: create vs index route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `create vs index route selection` and compare fields against the intended resource contract.
- Invariant to test: presenters must copy explicit fields rather than embed domain structs
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting the presenter's field set equals an explicit allowlist
