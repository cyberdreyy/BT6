# Q0283: redaction applied only on one route in bridges.NewBridgeResource

## Question
Is redaction in `NewBridgeResource` applied on the index route but not on show/export/create at the JSON:API response of /v2/bridge_types and job spec views, letting an authenticated node user holding only the 'view' role read the secret through the other route?

## Target
- File/function: [core/web/presenters/bridges.go](core/web/presenters/bridges.go) -> `NewBridgeResource`
- Entrypoint: the JSON:API response of /v2/bridge_types and job spec views
- Attacker controls: index vs show route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare `index vs show route selection` across all routes rendering the same resource.
- Invariant to test: redaction must be a property of the resource, not of one route
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test comparing the field set across routes
