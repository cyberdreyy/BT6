# Q3185: replay/reprocess trigger under-gated in jobs_controller.Show

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) force reprocessing of chain history through `Show` at POST/PATCH /v2/jobs (edit role) so the node re-emits or re-reports data derived from a range the attacker chose?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Show`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: update payload on an existing job (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `update payload on an existing job` with a crafted block range/chain id.
- Invariant to test: reprocessing must be admin-gated and range-validated
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test invoking the replay route from a low-role session
