# Q4483: authenticator precedence confusion in router.rateLimiter

## Question
Can an unauthenticated HTTP client that can reach the node API port send one request to any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) carrying both a crafted external-initiator credential and a session cookie so that the authenticator list reached by `rateLimiter` attributes the request to the stronger identity instead of failing closed?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `rateLimiter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: request body JSON (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `request body JSON` so an earlier authenticator errors and a later one succeeds while the request context still holds the first identity.
- Invariant to test: exactly one authenticator may establish identity, and a failed attempt must never leave a usable identity in the gin context
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over auth.Authenticate with mixed credential sets asserting the resolved user for each combination
