# Q4604: role wrapper omitted on a route in gql.GetGQLAuthenticatedSession

## Question
Is there a state-changing route reaching `GetGQLAuthenticatedSession` from POST /query (GraphQL) guarded by AuthenticateGQL that is registered without RequiresEditRole/RequiresAdminRole, letting an authenticated node user holding only the 'view' role invoke it with only view or run rights?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `GetGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: the session cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Enumerate registered routes and compare each handler's declared minimum role against its wrapper, then call the weakest one with `session cookie`.
- Invariant to test: every state-changing /v2 route must be wrapped by the role gate matching its side effect
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: reflective route-table test asserting each non-GET route carries a role middleware
