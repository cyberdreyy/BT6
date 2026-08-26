# Q2732: mutation missing its role assertion in api_token.Secret

## Question
Can an authenticated node user holding only the 'view' role execute the state-changing resolver `Secret` at POST /query createAPIToken/deleteAPIToken mutations because the resolver body omits authenticateUserCanEdit/IsAdmin while sibling resolvers include it?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `Secret`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: the password field in the mutation input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Select the resolver with `password field in the mutation input` from a view-role session.
- Invariant to test: every mutation must call the role assertion before touching state
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with view-role sessions
