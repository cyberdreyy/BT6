# Q0124: struct embedding pulls in secret fields in csa_key.NewCSAKeyResource

## Question
Does `NewCSAKeyResource` embed a domain struct so newly added secret fields are serialized automatically at the JSON:API response of /v2/keys/csa without anyone reviewing the response shape?

## Target
- File/function: [core/web/presenters/csa_key.go](core/web/presenters/csa_key.go) -> `NewCSAKeyResource`
- Entrypoint: the JSON:API response of /v2/keys/csa
- Attacker controls: index vs export route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `index vs export route selection` and compare fields against the intended resource contract.
- Invariant to test: presenters must copy explicit fields rather than embed domain structs
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting the presenter's field set equals an explicit allowlist
