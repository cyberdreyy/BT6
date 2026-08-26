# Q2553: export password not enforced in loop_registry.discoveryHandler

## Question
Can an authenticated node user holding only the 'view' role export key material through `discoveryHandler` at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) with an empty, default or attacker-chosen password, obtaining a bundle that is trivially decryptable offline?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `discoveryHandler`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: pprof query parameters (seconds, debug) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Call `pprof query parameters (seconds, debug)` with an empty/weak password parameter.
- Invariant to test: export must require the caller's authenticated proof and a strong password
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test exporting with an empty password and asserting failure
