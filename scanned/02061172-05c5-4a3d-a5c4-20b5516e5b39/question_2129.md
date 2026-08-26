# Q2129: introspection maps privileged surface in query.Bridges

## Question
Can an authenticated node user holding only the 'view' role use introspection at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) to enumerate the mutations guarded near `Bridges` and their inputs, then probe for the weakest one?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Bridges`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: the queried field and arguments (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Introspect with `queried field and arguments` and enumerate privileged fields.
- Invariant to test: if introspection is exposed, every field it reveals must still enforce its own role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting introspection-listed mutations all reject view-role sessions
