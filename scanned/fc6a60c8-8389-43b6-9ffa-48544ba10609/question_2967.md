# Q2967: rate limiter keyed on spoofable input in router.graphqlHandler

## Question
Can an unauthenticated HTTP client that can reach the node API port bypass the login/asset rate limiter reached by `graphqlHandler` by varying a client-controlled header used as the limiter key, enabling unbounded credential guessing against any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `graphqlHandler`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: the route path and HTTP verb (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `route path and HTTP verb` (X-Forwarded-For, session id) across requests.
- Invariant to test: the limiter key must be derived from server-observed connection identity
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: handler test sending N+1 requests with rotating forwarded-for headers asserting a 429
