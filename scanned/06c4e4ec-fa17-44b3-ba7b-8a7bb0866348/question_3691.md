# Q3691: resolver ignores soft-deleted state in query.Chain

## Question
Does `Chain` at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) resolve objects that are deleted/disabled, letting an authenticated node user holding only the 'view' role act through a decommissioned bridge, key or manager?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Chain`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: the queried field and arguments (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reference `queried field and arguments` for a deleted object.
- Invariant to test: resolvers must filter out deleted/disabled records
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: resolver test referencing deleted objects
