# Q1206: resolver ignores soft-deleted state in api_token.NewAPIToken

## Question
Does `NewAPIToken` at POST /query createAPIToken/deleteAPIToken mutations resolve objects that are deleted/disabled, letting an authenticated node user holding only the 'view' role act through a decommissioned bridge, key or manager?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `NewAPIToken`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: the password field in the mutation input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reference `password field in the mutation input` for a deleted object.
- Invariant to test: resolvers must filter out deleted/disabled records
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: resolver test referencing deleted objects
