# Q1810: verb/method override in gql.AuthenticateGQL

## Question
Does routing near `AuthenticateGQL` honour a method-override header or map an unexpected verb onto a state-changing handler, letting an authenticated node user holding only the 'view' role reach a write path through a read-gated route at POST /query (GraphQL) guarded by AuthenticateGQL?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `AuthenticateGQL`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: batched operations in one request (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `batched operations in one request` using HEAD/OPTIONS or an override header against write routes.
- Invariant to test: handler selection must depend only on the real HTTP method
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting non-declared verbs return 404/405 without executing the handler
