# Q3252: configuration dump includes secrets in keys_controller.Create

## Question
Does the configuration rendered by `Create` at /v2/keys/:keyType Index/Export/Import/Delete routes include secrets (DB URL with password, keystore password, API tokens, webhook or bridge credentials) visible to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Create`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the imported key JSON and its password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `imported key JSON and its password` and grep the response for credential patterns.
- Invariant to test: configuration output must be secret-redacted for every role
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: golden-file test asserting the rendered config contains no secret values
