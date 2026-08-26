# Q0792: content-encoding negotiation file selection in middleware.NewEmbedFileSystem

## Question
Can an unauthenticated HTTP client that can reach the node API port steer the file chosen by `NewEmbedFileSystem` via encoding negotiation on GET on any static asset path served by ServeGzippedAssets/GzipFileServer so a file outside the intended asset set is served?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `NewEmbedFileSystem`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: percent-encoded and dot-segment path bytes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Combine `percent-encoded and dot-segment path bytes` with crafted Accept-Encoding values that make the server append a suffix to an attacker-chosen path.
- Invariant to test: negotiation may only select among pre-registered asset variants
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test over findBestFile/negotiateContentEncoding with hostile paths and encodings
