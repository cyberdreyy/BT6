# Q0874: secret disclosure through error body in helpers.jsonAPIError

## Question
Does an error path reached from any /v2 or /query error response path through `jsonAPIError` serialize internal values (config secrets, DB DSN, key material, tokens) into the JSON:API error returned to an unauthenticated HTTP client that can reach the node API port?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `jsonAPIError`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: unknown IDs and type parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the error branch with `unknown IDs and type parameters` and inspect the returned detail string.
- Invariant to test: error responses must contain no server-side secret or connection string
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies match an allowlist of messages
