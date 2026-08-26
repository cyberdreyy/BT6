# Q5099: configuration dump includes secrets in loop_registry.pluginGroup

## Question
Does the configuration rendered by `pluginGroup` at the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers) include secrets (DB URL with password, keystore password, API tokens, webhook or bridge credentials) visible to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/loop_registry.go](core/web/loop_registry.go) -> `pluginGroup`
- Entrypoint: the LOOP plugin registry HTTP routes (discovery, per-plugin metrics and pprof handlers)
- Attacker controls: the POST /symbol body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `POST /symbol body` and grep the response for credential patterns.
- Invariant to test: configuration output must be secret-redacted for every role
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: golden-file test asserting the rendered config contains no secret values
