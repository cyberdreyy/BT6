# Q4861: transfer parameters under-validated in jobs_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) cause `Create` at POST/PATCH /v2/jobs (edit role) to send funds from a node-held key by controlling destination, amount, chain or balance-check flags?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Create`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: bridge names and external job id (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge names and external job id` with an attacker destination and a flag that skips the balance guard.
- Invariant to test: value transfers require admin authority and must validate destination, amount and chain
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test submitting a transfer from a non-admin session
