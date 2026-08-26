# Q5375: token secret returned on read in mutation.DeleteFeedsManagerChainConfig

## Question
Is the token secret produced by `DeleteFeedsManagerChainConfig` retrievable again at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) (on query or repeat mutation) so an authenticated node user holding only the 'view' role can read a secret issued to an admin?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `DeleteFeedsManagerChainConfig`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: multiple mutations batched in one document (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Query `multiple mutations batched in one document` after creation.
- Invariant to test: token secrets must be shown once, at creation, to their owner only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test asserting the secret is absent from all read paths
