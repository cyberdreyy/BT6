# Q3352: authorization oracle via response differences in middleware.Exists

## Question
Do the headers/status produced by `Exists` differ enough between 'no such object' and 'forbidden' on GET on any static asset path served by ServeGzippedAssets/GzipFileServer to let an unauthenticated HTTP client that can reach the node API port enumerate protected objects before escalating?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Exists`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: percent-encoded and dot-segment path bytes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare responses for `percent-encoded and dot-segment path bytes` across existing and non-existing identifiers.
- Invariant to test: authorization failures must be indistinguishable from missing objects
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting identical status/body for forbidden and missing resources
