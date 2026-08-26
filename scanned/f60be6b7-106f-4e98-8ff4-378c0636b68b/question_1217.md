# Q1217: log control abused to capture secrets in dkg_recipient_keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role change logging behaviour through `Index` at GET /v2/keys/dkgrecipient (level, SQL logging) so credentials or key material are written where a lower-privilege path can read them?

## Target
- File/function: [core/web/dkg_recipient_keys_controller.go](core/web/dkg_recipient_keys_controller.go) -> `Index`
- Entrypoint: GET /v2/keys/dkgrecipient
- Attacker controls: selected response fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Toggle `selected response fields` then trigger a credential-bearing request.
- Invariant to test: log configuration changes require admin authority and must not enable secret logging
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test toggling log settings from a low-role session
