# Q0975: delete/disable reachable below role in mutation.CreateBridge

## Question
Can an authenticated node user holding only the 'view' role disable or delete an object through `CreateBridge` at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) (feeds manager, bridge, key, job) with only view/run rights, degrading oracle reporting?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `CreateBridge`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: the mutation name and input object (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `mutation name and input object` from a low-role session.
- Invariant to test: destructive mutations require the admin role
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: resolver test invoking destructive mutations from low-role sessions
