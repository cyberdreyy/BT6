# Q2490: balance/attribute setters accept unvalidated input in eth_key.SetETHKeyLinkBalance

## Question
Can an authenticated node user holding only the 'view' role influence a value written by `SetETHKeyLinkBalance` before rendering at the JSON:API response of /v2/keys/evm and the ETH key formatter middleware (balance, max gas price, status) so an operator acts on falsified data?

## Target
- File/function: [core/web/presenters/eth_key.go](core/web/presenters/eth_key.go) -> `SetETHKeyLinkBalance`
- Entrypoint: the JSON:API response of /v2/keys/evm and the ETH key formatter middleware
- Attacker controls: export vs index route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `export vs index route selection` that flows into the setter.
- Invariant to test: rendered attributes must come from server-side state only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: unit test asserting setter inputs originate from trusted state
