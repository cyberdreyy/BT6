# Q2973: rate limiter keyed on spoofable input in helpers.paginatedResponse

## Question
Can an authenticated node user holding only the 'view' role bypass the login/asset rate limiter reached by `paginatedResponse` by varying a client-controlled header used as the limiter key, enabling unbounded credential guessing against the JSON:API response writer used by every /v2 controller?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedResponse`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: requested resource type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `requested resource type` (X-Forwarded-For, session id) across requests.
- Invariant to test: the limiter key must be derived from server-observed connection identity
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: handler test sending N+1 requests with rotating forwarded-for headers asserting a 429
