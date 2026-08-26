# Q0878: secret disclosure through error body in helpers.jsonAPIError

## Question
Does an error path reached from the JSON:API response writer used by every /v2 controller through `jsonAPIError` serialize internal values (config secrets, DB DSN, key material, tokens) into the JSON:API error returned to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `jsonAPIError`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: pagination parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the error branch with `pagination parameters` and inspect the returned detail string.
- Invariant to test: error responses must contain no server-side secret or connection string
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies match an allowlist of messages
