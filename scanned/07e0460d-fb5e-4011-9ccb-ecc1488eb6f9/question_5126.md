# Q5126: secret disclosure through error body in router.rateLimiter

## Question
Does an error path reached from any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) through `rateLimiter` serialize internal values (config secrets, DB DSN, key material, tokens) into the JSON:API error returned to an unauthenticated HTTP client that can reach the node API port?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `rateLimiter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: the route path and HTTP verb (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the error branch with `route path and HTTP verb` and inspect the returned detail string.
- Invariant to test: error responses must contain no server-side secret or connection string
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies match an allowlist of messages
