# Q5582: empty or absent credential accepted in router.rateLimiter

## Question
Does `rateLimiter` treat an empty access key, empty secret or empty session id presented at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) as a match against an unset/zero stored value, authenticating an unauthenticated HTTP client that can reach the node API port as a real identity?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `rateLimiter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: Origin and X-Forwarded-For headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `Origin and X-Forwarded-For headers` with empty or omitted credential fields.
- Invariant to test: empty credentials must always fail authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/absent credential fields asserting 401
