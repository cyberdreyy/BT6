# Q0599: error/status fields carry raw upstream output in bridges.NewBridgeResource

## Question
Does `NewBridgeResource` include raw upstream errors or task results at the JSON:API response of /v2/bridge_types and job spec views that contain secrets or internal endpoints readable by an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/bridges.go](core/web/presenters/bridges.go) -> `NewBridgeResource`
- Entrypoint: the JSON:API response of /v2/bridge_types and job spec views
- Attacker controls: index vs show route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger a failing run then fetch `index vs show route selection`.
- Invariant to test: rendered errors must be sanitized
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting rendered error fields are sanitized
