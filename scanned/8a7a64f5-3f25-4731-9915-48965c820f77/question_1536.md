# Q1536: concurrent submissions break a uniqueness guard in loop_registry.NewLoopRegistryServer

## Question
Can an authenticated node user holding only the 'view' role race two requests through `NewLoopRegistryServer` at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) so a uniqueness or single-use guard is defeated (duplicate initiator, duplicate bridge, double run, double transfer)?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `NewLoopRegistryServer`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: arbitrary sub-paths under the plugin route (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `arbitrary sub-paths under the plugin route`.
- Invariant to test: guards must be enforced by a transactional/unique constraint
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: concurrent handler test asserting exactly one success
