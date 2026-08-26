# Q3921: verb/method override in middleware.Exists

## Question
Does routing near `Exists` honour a method-override header or map an unexpected verb onto a state-changing handler, letting an unauthenticated HTTP client that can reach the node API port reach a write path through a read-gated route at GET on any static asset path served by ServeGzippedAssets/GzipFileServer?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Exists`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: range and conditional headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `range and conditional headers` using HEAD/OPTIONS or an override header against write routes.
- Invariant to test: handler selection must depend only on the real HTTP method
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting non-declared verbs return 404/405 without executing the handler
