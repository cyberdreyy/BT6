# Q1143: configuration dump includes secrets in replay_controller.ReplayFromBlock

## Question
Does the configuration rendered by `ReplayFromBlock` at POST /v2/replay_from_block/:number include secrets (DB URL with password, keystore password, API tokens, webhook or bridge credentials) visible to an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/replay_controller.go](core/web/replay_controller.go) -> `ReplayFromBlock`
- Entrypoint: POST /v2/replay_from_block/:number
- Attacker controls: the block number path parameter (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `block number path parameter` and grep the response for credential patterns.
- Invariant to test: configuration output must be secret-redacted for every role
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: golden-file test asserting the rendered config contains no secret values
