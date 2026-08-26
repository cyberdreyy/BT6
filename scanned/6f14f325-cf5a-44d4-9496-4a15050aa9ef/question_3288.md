# Q3288: chain selector reaches unintended relayer in middleware.Exists

## Question
Can an unauthenticated HTTP client that can reach the node API port supply a chain identifier through `Exists` at GET on any static asset path served by ServeGzippedAssets/GzipFileServer that resolves to a relayer/keystore other than the one authorization was evaluated against?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Exists`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: Accept-Encoding negotiation (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `Accept-Encoding negotiation` with alternate encodings of the chain id (leading zeros, whitespace, different base).
- Invariant to test: the chain resolved for execution must be the exact chain authorized for the request
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over getChain with equivalent-but-different chain id strings
