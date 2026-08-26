# Q4957: rate limiter keyed on spoofable input in auth.AuthenticateExternalInitiator

## Question
Can a holder of a restricted API access-key/secret pair bypass the login/asset rate limiter reached by `AuthenticateExternalInitiator` by varying a client-controlled header used as the limiter key, enabling unbounded credential guessing against any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateExternalInitiator`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: X-API-KEY and X-API-SECRET headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `X-API-KEY and X-API-SECRET headers` (X-Forwarded-For, session id) across requests.
- Invariant to test: the limiter key must be derived from server-observed connection identity
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: handler test sending N+1 requests with rotating forwarded-for headers asserting a 429
