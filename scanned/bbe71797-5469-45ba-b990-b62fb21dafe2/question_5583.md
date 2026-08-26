# Q5583: empty or absent credential accepted in middleware.Open

## Question
Does `Open` treat an empty access key, empty secret or empty session id presented at GET on any static asset path served by ServeGzippedAssets/GzipFileServer as a match against an unset/zero stored value, authenticating an unauthenticated HTTP client that can reach the node API port as a real identity?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Open`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: range and conditional headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `range and conditional headers` with empty or omitted credential fields.
- Invariant to test: empty credentials must always fail authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/absent credential fields asserting 401
