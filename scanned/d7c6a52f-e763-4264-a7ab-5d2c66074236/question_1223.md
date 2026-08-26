# Q1223: log control abused to capture secrets in log_controller.Patch

## Question
Can an authenticated node user holding only the 'view' role change logging behaviour through `Patch` at GET and PATCH /v2/log (level, SQL logging) so credentials or key material are written where a lower-privilege path can read them?

## Target
- File/function: [core/web/log_controller.go](core/web/log_controller.go) -> `Patch`
- Entrypoint: GET and PATCH /v2/log
- Attacker controls: repeated toggling of SQL logging (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Toggle `repeated toggling of SQL logging` then trigger a credential-bearing request.
- Invariant to test: log configuration changes require admin authority and must not enable secret logging
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test toggling log settings from a low-role session
