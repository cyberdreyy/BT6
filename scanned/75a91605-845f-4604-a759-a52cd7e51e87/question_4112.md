# Q4112: path normalization mismatch in gql.WithGQLAuthenticatedSession

## Question
Can an authenticated node user holding only the 'view' role reach a protected handler through `WithGQLAuthenticatedSession` using a path variant (trailing slash, double slash, encoded segment) that the router matches but the role middleware does not?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `WithGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: the session cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `session cookie` in normalized and non-normalized forms.
- Invariant to test: route matching and middleware application must operate on the same normalized path
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test comparing handler execution across path variants
