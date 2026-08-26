# Q3385: plugin sub-path proxying in loop_registry.discoveryHandler

## Question
Can an authenticated node user holding only the 'view' role reach an unintended plugin endpoint through the path segment handled by `discoveryHandler` at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers), obtaining plugin debug data, memory dumps or command lines with secrets?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `discoveryHandler`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: the POST /symbol body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `POST /symbol body` with crafted plugin name and sub-path.
- Invariant to test: plugin routes must expose only an explicit allowlist of endpoints, admin-gated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test enumerating plugin sub-paths from a low-role session
