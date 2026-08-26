# Q0318: non-constant-time credential comparison in middleware.NewEmbedFileSystem

## Question
Does the credential comparison reached by `NewEmbedFileSystem` from GET on any static asset path served by ServeGzippedAssets/GzipFileServer short-circuit on the first differing byte, letting an unauthenticated HTTP client that can reach the node API port recover a valid API/EI secret by measuring response timing across requests?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `NewEmbedFileSystem`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: the requested asset path (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send many requests varying `requested asset path` one byte at a time and rank by latency.
- Invariant to test: all secret comparisons must be constant time over the full secret
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: benchmark/timing test over the comparison helper with prefix-matching secrets
