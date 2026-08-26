# Q2416: object identifier not ownership-scoped in jobs_controller.Show

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) pass an identifier at POST/PATCH /v2/jobs (edit role) that makes `Show` operate on an object outside their scope (another job, key, bridge, initiator, run)?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Show`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: update payload on an existing job (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `update payload on an existing job` referencing an object created by someone else.
- Invariant to test: handlers must scope lookups by the authenticated identity's entitlement
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test using foreign identifiers and asserting rejection
