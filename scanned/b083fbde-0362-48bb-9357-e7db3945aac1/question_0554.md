# Q0554: credentialed cross-origin request in router.NewRouter

## Question
Does the origin handling on the path through `NewRouter` allow a browser page controlled by the attacker to send credentialed state-changing requests to any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) and read the response?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `NewRouter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: the session cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Serve a page that issues `session cookie` with credentials from an origin echoed back by the CORS logic.
- Invariant to test: credentialed responses may only be exposed to explicitly configured origins
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the origin matcher with attacker-controlled Origin values
