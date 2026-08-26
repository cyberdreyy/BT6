# Q0875: secret disclosure through error body in cookies.FindSessionCookie

## Question
Does an error path reached from the Cookie header on any authenticated /v2 route through `FindSessionCookie` serialize internal values (config secrets, DB DSN, key material, tokens) into the JSON:API error returned to an unauthenticated HTTP client that can reach the node API port?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie value encoding (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the error branch with `cookie value encoding` and inspect the returned detail string.
- Invariant to test: error responses must contain no server-side secret or connection string
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies match an allowlist of messages
