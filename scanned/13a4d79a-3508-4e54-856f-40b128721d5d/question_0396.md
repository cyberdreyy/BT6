# Q0396: multiple session cookies in router.NewRouter

## Question
If an unauthenticated HTTP client that can reach the node API port sends two clsession cookies on any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688), does the lookup used by `NewRouter` pick the attacker-supplied one while later code trusts the other, producing a session-identity mismatch?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `NewRouter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: the route path and HTTP verb (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `route path and HTTP verb` with duplicate cookie names in one header.
- Invariant to test: exactly one session cookie must be considered and duplicates must be rejected
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test issuing duplicate Cookie headers and asserting a 401
