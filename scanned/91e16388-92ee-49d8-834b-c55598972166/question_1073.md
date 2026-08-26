# Q1073: struct embedding pulls in secret fields in vault.NewExportDKGResultResource

## Question
Does `NewExportDKGResultResource` embed a domain struct so newly added secret fields are serialized automatically at the JSON:API response of /v2/vault/dkg_results/* without anyone reviewing the response shape?

## Target
- File/function: [core/web/presenters/vault.go](core/web/presenters/vault.go) -> `NewExportDKGResultResource`
- Entrypoint: the JSON:API response of /v2/vault/dkg_results/*
- Attacker controls: verify vs export route selection (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `verify vs export route selection` and compare fields against the intended resource contract.
- Invariant to test: presenters must copy explicit fields rather than embed domain structs
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting the presenter's field set equals an explicit allowlist
