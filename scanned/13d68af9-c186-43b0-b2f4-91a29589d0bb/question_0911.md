# Q0911: balance/limit guard bypass in loop_registry.NewLoopRegistryServer

## Question
Can an authenticated node user holding only the 'view' role bypass the balance or limit validation performed by `NewLoopRegistryServer` at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) through numeric parsing (overflow, negative, scientific notation, unit confusion)?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `NewLoopRegistryServer`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: arbitrary sub-paths under the plugin route (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `arbitrary sub-paths under the plugin route` in hostile numeric encodings.
- Invariant to test: amount validation must be exact over the canonical big-int representation
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over the amount parser and balance validator
