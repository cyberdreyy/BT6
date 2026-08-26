# Q0362: export bundle rendered to a non-owner in bridges.NewBridgeResource

## Question
Does `NewBridgeResource` render exported key material at the JSON:API response of /v2/bridge_types and job spec views to any caller passing the role gate rather than the key owner/admin only?

## Target
- File/function: [core/web/presenters/bridges.go](core/web/presenters/bridges.go) -> `NewBridgeResource`
- Entrypoint: the JSON:API response of /v2/bridge_types and job spec views
- Attacker controls: the bridge name requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `bridge name requested` from the weakest role accepted.
- Invariant to test: export material may only be rendered to an admin-authenticated owner
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test requesting the export from each role
