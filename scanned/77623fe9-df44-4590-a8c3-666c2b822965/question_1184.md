# Q1184: debug route reachable below intended role in middleware.NewEmbedFileSystem

## Question
Is a debug/pprof/metrics route wired near `NewEmbedFileSystem` reachable by an unauthenticated HTTP client that can reach the node API port at GET on any static asset path served by ServeGzippedAssets/GzipFileServer, exposing node memory, goroutine dumps or command lines containing key passwords?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `NewEmbedFileSystem`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: range and conditional headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `range and conditional headers` against the debug group with a low-privilege session.
- Invariant to test: debug endpoints must require the highest role and never be reachable unauthenticated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test hitting each debug path with view-role and anonymous sessions
