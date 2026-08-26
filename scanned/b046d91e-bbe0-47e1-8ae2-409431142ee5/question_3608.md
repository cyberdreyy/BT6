# Q3608: state-changing request without origin binding in gql.WithGQLAuthenticatedSession

## Question
Can a page loaded by a logged-in operator cause an authenticated node user holding only the 'view' role's chosen state change at POST /query (GraphQL) guarded by AuthenticateGQL through `WithGQLAuthenticatedSession` because the session cookie alone authorizes the mutation?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `WithGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: the session cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Auto-submit `session cookie` from an attacker page targeting a key-export or transfer route.
- Invariant to test: state-changing requests must require a non-cookie credential or origin binding
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test issuing a cross-site style request with only a session cookie
