# Q4838: GraphQL mutation reaches unguarded resolver in middleware.Open

## Question
Can an unauthenticated HTTP client that can reach the node API port invoke a state-changing resolver behind `Open` at GET on any static asset path served by ServeGzippedAssets/GzipFileServer because the role check is applied at the HTTP layer rather than per-resolver?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Open`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: percent-encoded and dot-segment path bytes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Post a document using `percent-encoded and dot-segment path bytes` that selects an admin-only mutation from a view-role session.
- Invariant to test: every mutation resolver must independently assert its minimum role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with a view-role session and asserting an authorization error
