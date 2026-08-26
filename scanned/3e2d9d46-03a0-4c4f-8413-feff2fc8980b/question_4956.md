# Q4956: rate limiter keyed on spoofable input in middleware.Open

## Question
Can an unauthenticated HTTP client that can reach the node API port bypass the login/asset rate limiter reached by `Open` by varying a client-controlled header used as the limiter key, enabling unbounded credential guessing against GET on any static asset path served by ServeGzippedAssets/GzipFileServer?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Open`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: the requested asset path (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `requested asset path` (X-Forwarded-For, session id) across requests.
- Invariant to test: the limiter key must be derived from server-observed connection identity
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: handler test sending N+1 requests with rotating forwarded-for headers asserting a 429
