# Q3542: route group ordering in router.graphqlHandler

## Question
Does the registration order around `graphqlHandler` place an unauthenticated group after an authenticated one so a path registered twice is served by the unauthenticated handler for an unauthenticated HTTP client that can reach the node API port?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `graphqlHandler`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: request body JSON (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `request body JSON` against paths registered in more than one group.
- Invariant to test: each path may be served by exactly one middleware chain, the most restrictive one
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route-table test asserting no path is registered in both authenticated and unauthenticated groups
