# Q0159: role wrapper omitted on a route in router.NewRouter

## Question
Is there a state-changing route reaching `NewRouter` from any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) that is registered without RequiresEditRole/RequiresAdminRole, letting an unauthenticated HTTP client that can reach the node API port invoke it with only view or run rights?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `NewRouter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: the session cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Enumerate registered routes and compare each handler's declared minimum role against its wrapper, then call the weakest one with `session cookie`.
- Invariant to test: every state-changing /v2 route must be wrapped by the role gate matching its side effect
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: reflective route-table test asserting each non-GET route carries a role middleware
