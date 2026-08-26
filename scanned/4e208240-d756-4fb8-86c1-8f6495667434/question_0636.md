# Q0636: rate limiter keyed on spoofable input in gql.AuthenticateGQL

## Question
Can an authenticated node user holding only the 'view' role bypass the login/asset rate limiter reached by `AuthenticateGQL` by varying a client-controlled header used as the limiter key, enabling unbounded credential guessing against POST /query (GraphQL) guarded by AuthenticateGQL?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `AuthenticateGQL`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: the GraphQL document (query/mutation/alias/fragment) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `GraphQL document (query/mutation/alias/fragment)` (X-Forwarded-For, session id) across requests.
- Invariant to test: the limiter key must be derived from server-observed connection identity
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: handler test sending N+1 requests with rotating forwarded-for headers asserting a 429
