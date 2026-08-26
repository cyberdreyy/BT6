# Q1144: configuration dump includes secrets in config_controller.Show

## Question
Does the configuration rendered by `Show` at GET /v2/config/v2 include secrets (DB URL with password, keystore password, API tokens, webhook or bridge credentials) visible to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/config_controller.go](core/web/config_controller.go) -> `Show`
- Entrypoint: GET /v2/config/v2
- Attacker controls: the request path and query parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `request path and query parameters` and grep the response for credential patterns.
- Invariant to test: configuration output must be secret-redacted for every role
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: golden-file test asserting the rendered config contains no secret values
