# Q0836: listing renders objects across owners in bridges.NewBridgeResource

## Question
Does the collection built by `NewBridgeResource` at the JSON:API response of /v2/bridge_types and job spec views render objects outside an authenticated node user holding only the 'view' role's entitlement together with their sensitive attributes?

## Target
- File/function: [core/web/presenters/bridges.go](core/web/presenters/bridges.go) -> `NewBridgeResource`
- Entrypoint: the JSON:API response of /v2/bridge_types and job spec views
- Attacker controls: the bridge name requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `bridge name requested` as a low-role user.
- Invariant to test: collections must be filtered before rendering
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing collection contents per role
