# Q0757: resource type confusion in bridges.NewBridgeResource

## Question
Can an authenticated node user holding only the 'view' role cause `NewBridgeResource` at the JSON:API response of /v2/bridge_types and job spec views to render one resource type with another's attribute set, exposing fields the intended presenter would redact?

## Target
- File/function: [core/web/presenters/bridges.go](core/web/presenters/bridges.go) -> `NewBridgeResource`
- Entrypoint: the JSON:API response of /v2/bridge_types and job spec views
- Attacker controls: index vs show route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `index vs show route selection` with a mismatched type/id.
- Invariant to test: the presenter selected must match the object type exactly
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over presenter selection for mismatched types
