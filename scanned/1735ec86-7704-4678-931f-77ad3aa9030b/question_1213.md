# Q1213: log control abused to capture secrets in bridge_types_controller.ValidateBridgeTypeNotExist

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) change logging behaviour through `ValidateBridgeTypeNotExist` at POST/PATCH/GET /v2/bridge_types (level, SQL logging) so credentials or key material are written where a lower-privilege path can read them?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeTypeNotExist`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: bridge name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Toggle `bridge name and URL` then trigger a credential-bearing request.
- Invariant to test: log configuration changes require admin authority and must not enable secret logging
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test toggling log settings from a low-role session
