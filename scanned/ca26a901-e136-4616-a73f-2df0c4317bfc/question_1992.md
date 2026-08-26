# Q1992: error text discloses key or file paths in dkg_recipient_keys_controller.Index

## Question
Do errors from `Index` at GET /v2/keys/dkgrecipient reveal keystore paths, key ids, addresses or DB structure that let an authenticated node user holding only the 'view' role target the next step of a key-theft chain?

## Target
- File/function: [core/web/dkg_recipient_keys_controller.go](core/web/dkg_recipient_keys_controller.go) -> `Index`
- Entrypoint: GET /v2/keys/dkgrecipient
- Attacker controls: selected response fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `selected response fields`.
- Invariant to test: errors must not disclose key identities or filesystem layout
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies exclude paths and key ids
