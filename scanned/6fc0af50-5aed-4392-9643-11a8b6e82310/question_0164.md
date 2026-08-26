# Q0164: role wrapper omitted on a route in cookies.FindSessionCookie

## Question
Is there a state-changing route reaching `FindSessionCookie` from the Cookie header on any authenticated /v2 route that is registered without RequiresEditRole/RequiresAdminRole, letting an unauthenticated HTTP client that can reach the node API port invoke it with only view or run rights?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie value encoding (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Enumerate registered routes and compare each handler's declared minimum role against its wrapper, then call the weakest one with `cookie value encoding`.
- Invariant to test: every state-changing /v2 route must be wrapped by the role gate matching its side effect
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: reflective route-table test asserting each non-GET route carries a role middleware
