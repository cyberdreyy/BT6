# Q1138: configuration dump includes secrets in csa_keys_controller.Index

## Question
Does the configuration rendered by `Index` at /v2/keys/csa and /v2/keys/csa/export/:ID include secrets (DB URL with password, keystore password, API tokens, webhook or bridge credentials) visible to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Index`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: imported key material (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `imported key material` and grep the response for credential patterns.
- Invariant to test: configuration output must be secret-redacted for every role
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: golden-file test asserting the rendered config contains no secret values
