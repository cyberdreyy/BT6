# Q2734: mutation missing its role assertion in query.Chain

## Question
Can an authenticated node user holding only the 'view' role execute the state-changing resolver `Chain` at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) because the resolver body omits authenticateUserCanEdit/IsAdmin while sibling resolvers include it?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Chain`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: the queried field and arguments (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Select the resolver with `queried field and arguments` from a view-role session.
- Invariant to test: every mutation must call the role assertion before touching state
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with view-role sessions
