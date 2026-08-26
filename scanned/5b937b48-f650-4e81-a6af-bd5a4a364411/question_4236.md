# Q4236: double decoding of identifiers in middleware.Exists

## Question
Is an identifier decoded twice between the authorization check and the lookup on the path through `Exists`, letting an unauthenticated HTTP client that can reach the node API port authorize one object at GET on any static asset path served by ServeGzippedAssets/GzipFileServer and act on another?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Exists`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: the requested asset path (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `requested asset path` percent-encoded so the two stages resolve to different values.
- Invariant to test: the value authorized and the value used must be byte-identical
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the authorized identifier equals the identifier passed to the store
