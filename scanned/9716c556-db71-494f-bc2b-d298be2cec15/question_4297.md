# Q4297: stale role after change in router.graphqlHandler

## Question
Does a session or token validated through `graphqlHandler` keep its old role at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) after the role was downgraded or the user deleted, letting an unauthenticated HTTP client that can reach the node API port act with revoked privileges?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `graphqlHandler`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: Authorization / X-API-KEY headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Continue sending `Authorization / X-API-KEY headers` on the existing session after the change.
- Invariant to test: role and existence must be re-read from the store on every request
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test downgrading a role mid-session and asserting the next request is rejected
