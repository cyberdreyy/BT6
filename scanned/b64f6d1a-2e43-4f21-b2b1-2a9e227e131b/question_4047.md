# Q4047: session fixation in middleware.Exists

## Question
Does the session id observed on the path through `Exists` survive privilege changes at GET on any static asset path served by ServeGzippedAssets/GzipFileServer, letting an unauthenticated HTTP client that can reach the node API port pre-seed a session id that becomes privileged after the victim logs in?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Exists`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: Accept-Encoding negotiation (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Plant `Accept-Encoding negotiation` and observe whether the id is regenerated on successful login.
- Invariant to test: a new session identifier must be issued on every successful authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the session id before and after login differ
