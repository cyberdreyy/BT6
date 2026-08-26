# Q4173: wildcard parameter swallows a route in middleware.Exists

## Question
Does a wildcard/param segment on the path to `Exists` capture a more specific protected route so an unauthenticated HTTP client that can reach the node API port's request at GET on any static asset path served by ServeGzippedAssets/GzipFileServer is served by a handler with weaker checks?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Exists`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: range and conditional headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `range and conditional headers` whose value equals another route's literal segment.
- Invariant to test: wildcard routes must not shadow explicitly registered protected routes
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting the expected handler runs for colliding paths
