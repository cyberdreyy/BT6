# Q2550: export password not enforced in csa_keys_controller.Create

## Question
Can an authenticated node user holding only the 'view' role export key material through `Create` at /v2/keys/csa and /v2/keys/csa/export/:ID with an empty, default or attacker-chosen password, obtaining a bundle that is trivially decryptable offline?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Create`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: the key ID path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Call `key ID path parameter` with an empty/weak password parameter.
- Invariant to test: export must require the caller's authenticated proof and a strong password
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test exporting with an empty password and asserting failure
