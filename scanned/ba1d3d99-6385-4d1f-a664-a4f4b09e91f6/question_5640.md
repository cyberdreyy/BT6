# Q5640: external-initiator credential over-scoped in middleware.Open

## Question
Can an unauthenticated HTTP client that can reach the node API port use an external-initiator credential accepted by `Open` on GET on any static asset path served by ServeGzippedAssets/GzipFileServer to reach routes beyond the single job-run endpoint it was issued for?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Open`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: the requested asset path (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `requested asset path` against other /v2 routes sharing the authenticator list.
- Invariant to test: an EI credential must authorize only run-triggering for jobs bound to that initiator
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: route test presenting EI credentials against every /v2 route
