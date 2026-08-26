# Q2389: index route serves privileged payload in middleware.NewEmbedFileSystem

## Question
Can an unauthenticated HTTP client that can reach the node API port obtain configuration, feature flags or identity data embedded by `NewEmbedFileSystem` into the index/asset response at GET on any static asset path served by ServeGzippedAssets/GzipFileServer without authenticating?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `NewEmbedFileSystem`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: range and conditional headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `range and conditional headers` anonymously and inspect the served document.
- Invariant to test: unauthenticated responses must contain no node configuration or identity data
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test fetching index/static routes anonymously and asserting a fixed payload
