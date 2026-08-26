# Q3121: state change without authorization ordering in jobs_controller.Show

## Question
Does `Show` at POST/PATCH /v2/jobs (edit role) mutate state before completing its authorization or validation, so an authenticated node user holding only the 'edit' role (non-admin) gets the effect together with the error?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Show`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: bridge names and external job id (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `bridge names and external job id` that fails late.
- Invariant to test: no state change may precede a completed authorization
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test asserting no mutation accompanies an error response
