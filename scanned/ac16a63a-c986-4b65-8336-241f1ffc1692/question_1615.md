# Q1615: balance/attribute setters accept unvalidated input in user.NewUserResources

## Question
Can an authenticated node user holding only the 'view' role influence a value written by `NewUserResources` before rendering at the JSON:API response of GET /v2/users and /sessions (balance, max gas price, status) so an operator acts on falsified data?

## Target
- File/function: [core/web/presenters/user.go](core/web/presenters/user.go) -> `NewUserResources`
- Entrypoint: the JSON:API response of GET /v2/users and /sessions
- Attacker controls: which resource fields are serialized (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `which resource fields are serialized` that flows into the setter.
- Invariant to test: rendered attributes must come from server-side state only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: unit test asserting setter inputs originate from trusted state
