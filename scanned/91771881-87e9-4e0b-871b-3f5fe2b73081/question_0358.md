# Q0358: import path plants attacker key material in loop_registry.NewLoopRegistryServer

## Question
Can an authenticated node user holding only the 'view' role import key material through `NewLoopRegistryServer` at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) so the node later signs oracle reports or transactions with an attacker-known key?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `NewLoopRegistryServer`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: the plugin name path segment (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `plugin name path segment` containing a key the attacker generated.
- Invariant to test: key import must be admin-only and validated
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test importing a key from a non-admin session
