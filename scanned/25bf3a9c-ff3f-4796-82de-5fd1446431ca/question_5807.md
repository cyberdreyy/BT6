# Q5807: verb/method override in router.rateLimiter

## Question
Does routing near `rateLimiter` honour a method-override header or map an unexpected verb onto a state-changing handler, letting an unauthenticated HTTP client that can reach the node API port reach a write path through a read-gated route at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `rateLimiter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: the session cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `session cookie` using HEAD/OPTIONS or an override header against write routes.
- Invariant to test: handler selection must depend only on the real HTTP method
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting non-declared verbs return 404/405 without executing the handler
