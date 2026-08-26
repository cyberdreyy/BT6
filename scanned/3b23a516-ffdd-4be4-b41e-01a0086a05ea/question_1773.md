# Q1773: listing renders objects across owners in csa_key.NewCSAKeyResources

## Question
Does the collection built by `NewCSAKeyResources` at the JSON:API response of /v2/keys/csa render objects outside an authenticated node user holding only the 'view' role's entitlement together with their sensitive attributes?

## Target
- File/function: [core/web/presenters/csa_key.go](core/web/presenters/csa_key.go) -> `NewCSAKeyResources`
- Entrypoint: the JSON:API response of /v2/keys/csa
- Attacker controls: the key id requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `key id requested` as a low-role user.
- Invariant to test: collections must be filtered before rendering
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing collection contents per role
