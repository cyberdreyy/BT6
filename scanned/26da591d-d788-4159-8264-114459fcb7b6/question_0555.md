# Q0555: credentialed cross-origin request in middleware.NewEmbedFileSystem

## Question
Does the origin handling on the path through `NewEmbedFileSystem` allow a browser page controlled by the attacker to send credentialed state-changing requests to GET on any static asset path served by ServeGzippedAssets/GzipFileServer and read the response?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `NewEmbedFileSystem`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: range and conditional headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Serve a page that issues `range and conditional headers` with credentials from an origin echoed back by the CORS logic.
- Invariant to test: credentialed responses may only be exposed to explicitly configured origins
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the origin matcher with attacker-controlled Origin values
