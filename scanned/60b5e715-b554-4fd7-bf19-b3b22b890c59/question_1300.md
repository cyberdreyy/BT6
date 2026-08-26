# Q1300: plugin sub-path proxying in config_controller.Show

## Question
Can an authenticated node user holding only the 'view' role reach an unintended plugin endpoint through the path segment handled by `Show` at GET /v2/config/v2, obtaining plugin debug data, memory dumps or command lines with secrets?

## Target
- File/function: [core/web/config_controller.go](core/web/config_controller.go) -> `Show`
- Entrypoint: GET /v2/config/v2
- Attacker controls: the request path and query parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `request path and query parameters` with crafted plugin name and sub-path.
- Invariant to test: plugin routes must expose only an explicit allowlist of endpoints, admin-gated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test enumerating plugin sub-paths from a low-role session
