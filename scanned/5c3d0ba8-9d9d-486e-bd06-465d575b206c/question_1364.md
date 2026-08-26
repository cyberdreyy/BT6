# Q1364: resolver executes before auth on error in query.Bridge

## Question
Does `Bridge` at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) perform its side effect before its role assertion returns, so an authenticated node user holding only the 'view' role still causes the change while receiving an authorization error?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Bridge`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: pagination arguments (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `pagination arguments` and inspect state afterwards.
- Invariant to test: authorization must complete before any side effect
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test asserting no state change accompanies an authorization error
