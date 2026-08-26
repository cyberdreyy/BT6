# Q1651: identity overwritten downstream in router.NewRouter

## Question
Can a later middleware or handler on the path through `NewRouter` overwrite the authenticated identity established at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) using a request-controlled field?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `NewRouter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: Authorization / X-API-KEY headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `Authorization / X-API-KEY headers` whose name collides with the context key or session field used downstream.
- Invariant to test: the authenticated identity must be immutable after the auth middleware
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test injecting colliding body/header fields and asserting the identity is unchanged
