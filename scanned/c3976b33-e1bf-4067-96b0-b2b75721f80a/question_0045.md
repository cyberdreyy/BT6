# Q0045: secret field serialized in csa_key.NewCSAKeyResource

## Question
Does the resource built by `NewCSAKeyResource` for the JSON:API response of /v2/keys/csa include a secret field (private key, seed, token, password, DSN, share) that an authenticated node user holding only the 'view' role can read?

## Target
- File/function: [core/web/presenters/csa_key.go](core/web/presenters/csa_key.go) -> `NewCSAKeyResource`
- Entrypoint: the JSON:API response of /v2/keys/csa
- Attacker controls: the key id requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `key id requested` and inspect the JSON:API attributes.
- Invariant to test: presenters must whitelist non-secret attributes only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: golden-file test over the presenter output
