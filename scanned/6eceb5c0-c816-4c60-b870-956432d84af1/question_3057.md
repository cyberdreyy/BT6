# Q3057: balance/limit guard bypass in jobs_controller.Show

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) bypass the balance or limit validation performed by `Show` at POST/PATCH /v2/jobs (edit role) through numeric parsing (overflow, negative, scientific notation, unit confusion)?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Show`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: spec type and pipeline DAG (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `spec type and pipeline DAG` in hostile numeric encodings.
- Invariant to test: amount validation must be exact over the canonical big-int representation
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over the amount parser and balance validator
