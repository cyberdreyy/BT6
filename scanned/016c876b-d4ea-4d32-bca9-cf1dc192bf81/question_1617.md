# Q1617: balance/attribute setters accept unvalidated input in csa_key.NewCSAKeyResources

## Question
Can an authenticated node user holding only the 'view' role influence a value written by `NewCSAKeyResources` before rendering at the JSON:API response of /v2/keys/csa (balance, max gas price, status) so an operator acts on falsified data?

## Target
- File/function: [core/web/presenters/csa_key.go](core/web/presenters/csa_key.go) -> `NewCSAKeyResources`
- Entrypoint: the JSON:API response of /v2/keys/csa
- Attacker controls: the key id requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `key id requested` that flows into the setter.
- Invariant to test: rendered attributes must come from server-side state only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: unit test asserting setter inputs originate from trusted state
