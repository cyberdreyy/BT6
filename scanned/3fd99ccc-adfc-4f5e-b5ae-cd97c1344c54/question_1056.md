# Q1056: replay/reprocess trigger under-gated in external_initiators_controller.ValidateExternalInitiator

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) force reprocessing of chain history through `ValidateExternalInitiator` at POST/DELETE /v2/external_initiators so the node re-emits or re-reports data derived from a range the attacker chose?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `ValidateExternalInitiator`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: returned credential fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `returned credential fields` with a crafted block range/chain id.
- Invariant to test: reprocessing must be admin-gated and range-validated
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test invoking the replay route from a low-role session
