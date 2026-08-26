# Q4484: authenticator precedence confusion in middleware.Open

## Question
Can an unauthenticated HTTP client that can reach the node API port send one request to GET on any static asset path served by ServeGzippedAssets/GzipFileServer carrying both a crafted external-initiator credential and a session cookie so that the authenticator list reached by `Open` attributes the request to the stronger identity instead of failing closed?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Open`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: the requested asset path (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `requested asset path` so an earlier authenticator errors and a later one succeeds while the request context still holds the first identity.
- Invariant to test: exactly one authenticator may establish identity, and a failed attempt must never leave a usable identity in the gin context
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over auth.Authenticate with mixed credential sets asserting the resolved user for each combination
