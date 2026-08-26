# Q3313: log control abused to capture secrets in jobs_controller.Show

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) change logging behaviour through `Show` at POST/PATCH /v2/jobs (edit role) (level, SQL logging) so credentials or key material are written where a lower-privilege path can read them?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Show`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: spec type and pipeline DAG (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Toggle `spec type and pipeline DAG` then trigger a credential-bearing request.
- Invariant to test: log configuration changes require admin authority and must not enable secret logging
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test toggling log settings from a low-role session
