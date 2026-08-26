# Q5206: plugin sub-path proxying in jobs_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) reach an unintended plugin endpoint through the path segment handled by `Create` at POST/PATCH /v2/jobs (edit role), obtaining plugin debug data, memory dumps or command lines with secrets?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Create`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: the TOML job spec body (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `TOML job spec body` with crafted plugin name and sub-path.
- Invariant to test: plugin routes must expose only an explicit allowlist of endpoints, admin-gated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test enumerating plugin sub-paths from a low-role session
