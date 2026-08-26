# Q0895: mutation reuses the authenticated session for another user in query.Bridge

## Question
Does `Bridge` at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) act on the identity named in the input rather than the session identity, letting an authenticated node user holding only the 'view' role operate as an admin?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Bridge`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: pagination arguments (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `pagination arguments` naming another user.
- Invariant to test: mutations must derive the acting identity from the session only
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test asserting the acted-on identity equals the session identity
