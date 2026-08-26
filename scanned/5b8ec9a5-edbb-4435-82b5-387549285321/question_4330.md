# Q4330: object identifier not ownership-scoped in loop_registry.pluginGroup

## Question
Can an authenticated node user holding only the 'view' role pass an identifier at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) that makes `pluginGroup` operate on an object outside their scope (another job, key, bridge, initiator, run)?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `pluginGroup`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: pprof query parameters (seconds, debug) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `pprof query parameters (seconds, debug)` referencing an object created by someone else.
- Invariant to test: handlers must scope lookups by the authenticated identity's entitlement
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test using foreign identifiers and asserting rejection
