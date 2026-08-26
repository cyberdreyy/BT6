# Q1811: verb/method override in helpers.jsonAPIError

## Question
Does routing near `jsonAPIError` honour a method-override header or map an unexpected verb onto a state-changing handler, letting an unauthenticated HTTP client that can reach the node API port reach a write path through a read-gated route at any /v2 or /query error response path?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `jsonAPIError`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: unknown IDs and type parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `unknown IDs and type parameters` using HEAD/OPTIONS or an override header against write routes.
- Invariant to test: handler selection must depend only on the real HTTP method
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting non-declared verbs return 404/405 without executing the handler
