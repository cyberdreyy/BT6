# Q4017: chain id selects an unauthorized keystore in loop_registry.discoveryHandler

## Question
Can an authenticated node user holding only the 'view' role pick a chain identifier at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) that makes `discoveryHandler` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `discoveryHandler`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: the plugin name path segment (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `plugin name path segment` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
