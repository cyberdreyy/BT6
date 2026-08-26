# Q5811: verb/method override in api.nextLink

## Question
Does routing near `nextLink` honour a method-override header or map an unexpected verb onto a state-changing handler, letting an authenticated node user holding only the 'view' role reach a write path through a read-gated route at page/size query parameters on /v2 index endpoints?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `nextLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: page and size query values (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `page and size query values` using HEAD/OPTIONS or an override header against write routes.
- Invariant to test: handler selection must depend only on the real HTTP method
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting non-declared verbs return 404/405 without executing the handler
