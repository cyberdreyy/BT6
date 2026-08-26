# Q5715: key-creating mutation reachable below role in query.sortByNetworkAndID

## Question
Can an authenticated node user holding only the 'view' role create or import a key through `sortByNetworkAndID` at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) without admin rights, planting a key the node will later sign with?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `sortByNetworkAndID`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: nested selection into key/secret-bearing types (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `nested selection into key/secret-bearing types` with attacker-supplied key material.
- Invariant to test: key material mutations require the admin role
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: resolver test creating/importing keys from low-role sessions
