# Q1305: export bundle rendered to a non-owner in csa_key.NewCSAKeyResources

## Question
Does `NewCSAKeyResources` render exported key material at the JSON:API response of /v2/keys/csa to any caller passing the role gate rather than the key owner/admin only?

## Target
- File/function: [core/web/presenters/csa_key.go](core/web/presenters/csa_key.go) -> `NewCSAKeyResources`
- Entrypoint: the JSON:API response of /v2/keys/csa
- Attacker controls: the key id requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `key id requested` from the weakest role accepted.
- Invariant to test: export material may only be rendered to an admin-authenticated owner
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test requesting the export from each role
