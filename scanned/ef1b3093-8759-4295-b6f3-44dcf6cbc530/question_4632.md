# Q4632: spec fields reach outbound requests with node credentials in loop_registry.pluginGroup

## Question
Can an authenticated node user holding only the 'view' role point a URL/host field accepted by `pluginGroup` at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) at an internal address or attacker host so the node performs a request carrying its own credentials or secrets?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `pluginGroup`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: the POST /symbol body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `POST /symbol body` with an internal or attacker URL.
- Invariant to test: outbound targets from user-supplied specs must be validated and never carry node credentials
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the URL validator with internal/attacker targets
