# Q1220: log control abused to capture secrets in evm_transfer_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) change logging behaviour through `Create` at POST /v2/transfers/evm (level, SQL logging) so credentials or key material are written where a lower-privilege path can read them?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `Create`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: gas limit and token contract fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Toggle `gas limit and token contract fields` then trigger a credential-bearing request.
- Invariant to test: log configuration changes require admin authority and must not enable secret logging
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test toggling log settings from a low-role session
