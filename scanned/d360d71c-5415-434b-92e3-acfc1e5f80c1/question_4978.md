# Q4978: state change without authorization ordering in jobs_controller.Create

## Question
Does `Create` at POST/PATCH /v2/jobs (edit role) mutate state before completing its authorization or validation, so an authenticated node user holding only the 'edit' role (non-admin) gets the effect together with the error?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Create`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: the TOML job spec body (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `TOML job spec body` that fails late.
- Invariant to test: no state change may precede a completed authorization
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test asserting no mutation accompanies an error response
