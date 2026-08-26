# Q4798: resolver ignores soft-deleted state in user.NewUpdatePasswordPayload

## Question
Does `NewUpdatePasswordPayload` at POST /query updateUserPassword mutation and user query resolve objects that are deleted/disabled, letting an authenticated node user holding only the 'view' role act through a decommissioned bridge, key or manager?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `NewUpdatePasswordPayload`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: selection set on the User type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reference `selection set on the User type` for a deleted object.
- Invariant to test: resolvers must filter out deleted/disabled records
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: resolver test referencing deleted objects
