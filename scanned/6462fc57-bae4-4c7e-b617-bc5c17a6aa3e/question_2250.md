# Q2250: stale role after change in middleware.NewEmbedFileSystem

## Question
Does a session or token validated through `NewEmbedFileSystem` keep its old role at GET on any static asset path served by ServeGzippedAssets/GzipFileServer after the role was downgraded or the user deleted, letting an unauthenticated HTTP client that can reach the node API port act with revoked privileges?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `NewEmbedFileSystem`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: Accept-Encoding negotiation (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Continue sending `Accept-Encoding negotiation` on the existing session after the change.
- Invariant to test: role and existence must be re-read from the store on every request
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test downgrading a role mid-session and asserting the next request is rejected
