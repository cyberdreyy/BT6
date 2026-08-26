# Q4779: multiple session cookies in middleware.Open

## Question
If an unauthenticated HTTP client that can reach the node API port sends two clsession cookies on GET on any static asset path served by ServeGzippedAssets/GzipFileServer, does the lookup used by `Open` pick the attacker-supplied one while later code trusts the other, producing a session-identity mismatch?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Open`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: Accept-Encoding negotiation (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `Accept-Encoding negotiation` with duplicate cookie names in one header.
- Invariant to test: exactly one session cookie must be considered and duplicates must be rejected
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test issuing duplicate Cookie headers and asserting a 401
