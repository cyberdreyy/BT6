# Q5659: delete/disable reachable below role in query.sortByNetworkAndID

## Question
Can an authenticated node user holding only the 'view' role disable or delete an object through `sortByNetworkAndID` at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) (feeds manager, bridge, key, job) with only view/run rights, degrading oracle reporting?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `sortByNetworkAndID`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: the queried field and arguments (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `queried field and arguments` from a low-role session.
- Invariant to test: destructive mutations require the admin role
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: resolver test invoking destructive mutations from low-role sessions
