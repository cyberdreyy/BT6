# Q1620: balance/attribute setters accept unvalidated input in job.NewFluxMonitorSpec

## Question
Can an authenticated node user holding only the 'view' role influence a value written by `NewFluxMonitorSpec` before rendering at the JSON:API response of GET /v2/jobs and /v2/jobs/:ID (balance, max gas price, status) so an operator acts on falsified data?

## Target
- File/function: [core/web/presenters/job.go](core/web/presenters/job.go) -> `NewFluxMonitorSpec`
- Entrypoint: the JSON:API response of GET /v2/jobs and /v2/jobs/:ID
- Attacker controls: pipeline spec fields returned (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `pipeline spec fields returned` that flows into the setter.
- Invariant to test: rendered attributes must come from server-side state only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: unit test asserting setter inputs originate from trusted state
