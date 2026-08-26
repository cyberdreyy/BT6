# Q3951: error text discloses key or file paths in csa_keys_controller.Create

## Question
Do errors from `Create` at /v2/keys/csa and /v2/keys/csa/export/:ID reveal keystore paths, key ids, addresses or DB structure that let an authenticated node user holding only the 'view' role target the next step of a key-theft chain?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Create`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: the export password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `export password`.
- Invariant to test: errors must not disclose key identities or filesystem layout
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies exclude paths and key ids
