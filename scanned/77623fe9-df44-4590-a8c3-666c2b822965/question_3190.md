# Q3190: replay/reprocess trigger under-gated in csa_keys_controller.Create

## Question
Can an authenticated node user holding only the 'view' role force reprocessing of chain history through `Create` at /v2/keys/csa and /v2/keys/csa/export/:ID so the node re-emits or re-reports data derived from a range the attacker chose?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Create`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: the export password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `export password` with a crafted block range/chain id.
- Invariant to test: reprocessing must be admin-gated and range-validated
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test invoking the replay route from a low-role session
