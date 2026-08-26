# Q0680: balance/attribute setters accept unvalidated input in vault.NewVerifyDKGResultResource

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) influence a value written by `NewVerifyDKGResultResource` before rendering at the JSON:API response of /v2/vault/dkg_results/* (balance, max gas price, status) so an operator acts on falsified data?

## Target
- File/function: [core/web/presenters/vault.go](core/web/presenters/vault.go) -> `NewVerifyDKGResultResource`
- Entrypoint: the JSON:API response of /v2/vault/dkg_results/*
- Attacker controls: the DKG result requested (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `DKG result requested` that flows into the setter.
- Invariant to test: rendered attributes must come from server-side state only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: unit test asserting setter inputs originate from trusted state
