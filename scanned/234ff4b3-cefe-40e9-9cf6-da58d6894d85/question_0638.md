# Q0638: rate limiter keyed on spoofable input in cookies.FindSessionCookie

## Question
Can an unauthenticated HTTP client that can reach the node API port bypass the login/asset rate limiter reached by `FindSessionCookie` by varying a client-controlled header used as the limiter key, enabling unbounded credential guessing against the Cookie header on any authenticated /v2 route?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie value encoding (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `cookie value encoding` (X-Forwarded-For, session id) across requests.
- Invariant to test: the limiter key must be derived from server-observed connection identity
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: handler test sending N+1 requests with rotating forwarded-for headers asserting a 429
