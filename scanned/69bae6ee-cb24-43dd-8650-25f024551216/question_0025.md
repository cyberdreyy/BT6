# Q0025: mutation missing its role assertion in user.NewUser

## Question
Can an authenticated node user holding only the 'view' role execute the state-changing resolver `NewUser` at POST /query updateUserPassword mutation and user query because the resolver body omits authenticateUserCanEdit/IsAdmin while sibling resolvers include it?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `NewUser`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: oldPassword/newPassword input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Select the resolver with `oldPassword/newPassword input` from a view-role session.
- Invariant to test: every mutation must call the role assertion before touching state
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with view-role sessions
