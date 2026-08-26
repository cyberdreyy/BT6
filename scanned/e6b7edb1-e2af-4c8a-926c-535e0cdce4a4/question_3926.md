# Q3926: verb/method override in helpers.paginatedResponse

## Question
Does routing near `paginatedResponse` honour a method-override header or map an unexpected verb onto a state-changing handler, letting an authenticated node user holding only the 'view' role reach a write path through a read-gated route at the JSON:API response writer used by every /v2 controller?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedResponse`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: requested resource type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `requested resource type` using HEAD/OPTIONS or an override header against write routes.
- Invariant to test: handler selection must depend only on the real HTTP method
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting non-declared verbs return 404/405 without executing the handler
