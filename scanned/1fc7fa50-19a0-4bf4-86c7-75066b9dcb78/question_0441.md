# Q0441: identifier reveals sensitive identity in bridges.NewBridgeResource

## Question
Does the identifier or metadata rendered by `NewBridgeResource` at the JSON:API response of /v2/bridge_types and job spec views reveal key identities, addresses or credential fingerprints that let an authenticated node user holding only the 'view' role target key theft or fund movement?

## Target
- File/function: [core/web/presenters/bridges.go](core/web/presenters/bridges.go) -> `NewBridgeResource`
- Entrypoint: the JSON:API response of /v2/bridge_types and job spec views
- Attacker controls: index vs show route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `index vs show route selection` at the lowest role.
- Invariant to test: identity metadata must be limited to what the caller's role needs
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing rendered identifiers per role
