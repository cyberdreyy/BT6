# Q5127: secret disclosure through error body in middleware.Open

## Question
Does an error path reached from GET on any static asset path served by ServeGzippedAssets/GzipFileServer through `Open` serialize internal values (config secrets, DB DSN, key material, tokens) into the JSON:API error returned to an unauthenticated HTTP client that can reach the node API port?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Open`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: range and conditional headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the error branch with `range and conditional headers` and inspect the returned detail string.
- Invariant to test: error responses must contain no server-side secret or connection string
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies match an allowlist of messages
