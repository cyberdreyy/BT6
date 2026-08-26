# Q1418: state-changing request without origin binding in middleware.NewEmbedFileSystem

## Question
Can a page loaded by a logged-in operator cause an unauthenticated HTTP client that can reach the node API port's chosen state change at GET on any static asset path served by ServeGzippedAssets/GzipFileServer through `NewEmbedFileSystem` because the session cookie alone authorizes the mutation?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `NewEmbedFileSystem`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: percent-encoded and dot-segment path bytes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Auto-submit `percent-encoded and dot-segment path bytes` from an attacker page targeting a key-export or transfer route.
- Invariant to test: state-changing requests must require a non-cookie credential or origin binding
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test issuing a cross-site style request with only a session cookie
