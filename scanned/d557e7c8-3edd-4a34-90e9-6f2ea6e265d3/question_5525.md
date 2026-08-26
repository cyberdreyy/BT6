# Q5525: state-changing request without origin binding in router.rateLimiter

## Question
Can a page loaded by a logged-in operator cause an unauthenticated HTTP client that can reach the node API port's chosen state change at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) through `rateLimiter` because the session cookie alone authorizes the mutation?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `rateLimiter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: the session cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Auto-submit `session cookie` from an attacker page targeting a key-export or transfer route.
- Invariant to test: state-changing requests must require a non-cookie credential or origin binding
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test issuing a cross-site style request with only a session cookie
