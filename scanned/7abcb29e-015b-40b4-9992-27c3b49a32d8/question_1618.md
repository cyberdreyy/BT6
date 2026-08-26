# Q1618: balance/attribute setters accept unvalidated input in external_initiators.NewExternalInitiatorResource

## Question
Can an authenticated node user holding only the 'view' role influence a value written by `NewExternalInitiatorResource` before rendering at the JSON:API response of /v2/external_initiators (balance, max gas price, status) so an operator acts on falsified data?

## Target
- File/function: [core/web/presenters/external_initiators.go](core/web/presenters/external_initiators.go) -> `NewExternalInitiatorResource`
- Entrypoint: the JSON:API response of /v2/external_initiators
- Attacker controls: the initiator requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `initiator requested` that flows into the setter.
- Invariant to test: rendered attributes must come from server-side state only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: unit test asserting setter inputs originate from trusted state
