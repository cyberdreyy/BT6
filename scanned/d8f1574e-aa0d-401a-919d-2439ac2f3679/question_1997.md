# Q1997: error text discloses key or file paths in config_controller.Show

## Question
Do errors from `Show` at GET /v2/config/v2 reveal keystore paths, key ids, addresses or DB structure that let an authenticated node user holding only the 'view' role target the next step of a key-theft chain?

## Target
- File/function: [core/web/config_controller.go](core/web/config_controller.go) -> `Show`
- Entrypoint: GET /v2/config/v2
- Attacker controls: Accept header / response format (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `Accept header / response format`.
- Invariant to test: errors must not disclose key identities or filesystem layout
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies exclude paths and key ids
