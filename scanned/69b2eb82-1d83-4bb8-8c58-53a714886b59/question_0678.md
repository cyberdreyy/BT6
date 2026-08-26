# Q0678: balance/attribute setters accept unvalidated input in bridges.NewBridgeResource

## Question
Can an authenticated node user holding only the 'view' role influence a value written by `NewBridgeResource` before rendering at the JSON:API response of /v2/bridge_types and job spec views (balance, max gas price, status) so an operator acts on falsified data?

## Target
- File/function: [core/web/presenters/bridges.go](core/web/presenters/bridges.go) -> `NewBridgeResource`
- Entrypoint: the JSON:API response of /v2/bridge_types and job spec views
- Attacker controls: the bridge name requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge name requested` that flows into the setter.
- Invariant to test: rendered attributes must come from server-side state only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: unit test asserting setter inputs originate from trusted state
