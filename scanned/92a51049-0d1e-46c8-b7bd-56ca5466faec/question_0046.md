# Q0046: secret field serialized in bridges.NewBridgeResource

## Question
Does the resource built by `NewBridgeResource` for the JSON:API response of /v2/bridge_types and job spec views include a secret field (private key, seed, token, password, DSN, share) that an authenticated node user holding only the 'view' role can read?

## Target
- File/function: [core/web/presenters/bridges.go](core/web/presenters/bridges.go) -> `NewBridgeResource`
- Entrypoint: the JSON:API response of /v2/bridge_types and job spec views
- Attacker controls: the bridge name requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `bridge name requested` and inspect the JSON:API attributes.
- Invariant to test: presenters must whitelist non-secret attributes only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: golden-file test over the presenter output
