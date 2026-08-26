# Q3251: configuration dump includes secrets in bridge_types_controller.ValidateBridgeType

## Question
Does the configuration rendered by `ValidateBridgeType` at POST/PATCH/GET /v2/bridge_types include secrets (DB URL with password, keystore password, API tokens, webhook or bridge credentials) visible to an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeType`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: confirmations and minimum contract payment (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `confirmations and minimum contract payment` and grep the response for credential patterns.
- Invariant to test: configuration output must be secret-redacted for every role
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: golden-file test asserting the rendered config contains no secret values
