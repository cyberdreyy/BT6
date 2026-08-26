# Q0277: export password not enforced in config_controller.Show

## Question
Can an authenticated node user holding only the 'view' role export key material through `Show` at GET /v2/config/v2 with an empty, default or attacker-chosen password, obtaining a bundle that is trivially decryptable offline?

## Target
- File/function: [core/web/config_controller.go](core/web/config_controller.go) -> `Show`
- Entrypoint: GET /v2/config/v2
- Attacker controls: Accept header / response format (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Call `Accept header / response format` with an empty/weak password parameter.
- Invariant to test: export must require the caller's authenticated proof and a strong password
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test exporting with an empty password and asserting failure
