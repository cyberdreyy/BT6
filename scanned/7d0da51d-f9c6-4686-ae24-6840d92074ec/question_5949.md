# Q5949: read route exposes a write-only field in loop_registry.pluginGroup

## Question
Does the read path through `pluginGroup` at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) return a field intended to be write-only (token, password, secret, private URL) to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `pluginGroup`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: pprof query parameters (seconds, debug) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `pprof query parameters (seconds, debug)` after creating the object.
- Invariant to test: write-only fields must never be readable
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting write-only fields are absent from reads
