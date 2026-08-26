# Q0027: mutation missing its role assertion in mutation.CreateBridge

## Question
Can an authenticated node user holding only the 'view' role execute the state-changing resolver `CreateBridge` at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) because the resolver body omits authenticateUserCanEdit/IsAdmin while sibling resolvers include it?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `CreateBridge`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: the mutation name and input object (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Select the resolver with `mutation name and input object` from a view-role session.
- Invariant to test: every mutation must call the role assertion before touching state
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with view-role sessions
