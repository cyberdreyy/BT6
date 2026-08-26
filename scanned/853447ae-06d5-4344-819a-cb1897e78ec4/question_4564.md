# Q4564: mutation reuses the authenticated session for another user in mutation.CreateFeedsManagerChainConfig

## Question
Does `CreateFeedsManagerChainConfig` at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) act on the identity named in the input rather than the session identity, letting an authenticated node user holding only the 'view' role operate as an admin?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `CreateFeedsManagerChainConfig`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: id arguments referencing other users' objects (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `id arguments referencing other users' objects` naming another user.
- Invariant to test: mutations must derive the acting identity from the session only
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test asserting the acted-on identity equals the session identity
