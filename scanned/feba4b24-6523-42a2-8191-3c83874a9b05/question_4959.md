# Q4959: rate limiter keyed on spoofable input in api.nextLink

## Question
Can an authenticated node user holding only the 'view' role bypass the login/asset rate limiter reached by `nextLink` by varying a client-controlled header used as the limiter key, enabling unbounded credential guessing against page/size query parameters on /v2 index endpoints?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `nextLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: page and size query values (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `page and size query values` (X-Forwarded-For, session id) across requests.
- Invariant to test: the limiter key must be derived from server-observed connection identity
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: handler test sending N+1 requests with rotating forwarded-for headers asserting a 429
