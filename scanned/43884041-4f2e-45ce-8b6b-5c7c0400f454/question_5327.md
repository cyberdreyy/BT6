# Q5327: identifier-to-object confusion across types in loop_registry.pluginGroup

## Question
Can an authenticated node user holding only the 'view' role supply an identifier of the wrong type/namespace at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) so `pluginGroup` resolves a different object class with weaker checks?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `pluginGroup`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: the POST /symbol body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `POST /symbol body` using another object's identifier format.
- Invariant to test: identifiers must be type- and namespace-checked before lookup
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test passing cross-type identifiers
