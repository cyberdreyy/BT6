# Q0204: custom marshaller leaks on error in bridges.NewBridgeResource

## Question
Does the marshalling path around `NewBridgeResource` fall back to default struct marshalling on error at the JSON:API response of /v2/bridge_types and job spec views, exposing unredacted fields to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/bridges.go](core/web/presenters/bridges.go) -> `NewBridgeResource`
- Entrypoint: the JSON:API response of /v2/bridge_types and job spec views
- Attacker controls: the bridge name requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the error branch via `bridge name requested`.
- Invariant to test: marshalling failure must produce an error, never a raw dump
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test forcing marshal errors and asserting no raw payload
