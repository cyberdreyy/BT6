# Q1057: replay/reprocess trigger under-gated in bridge_types_controller.ValidateBridgeTypeNotExist

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) force reprocessing of chain history through `ValidateBridgeTypeNotExist` at POST/PATCH/GET /v2/bridge_types so the node re-emits or re-reports data derived from a range the attacker chose?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeTypeNotExist`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: incoming/outgoing token fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `incoming/outgoing token fields` with a crafted block range/chain id.
- Invariant to test: reprocessing must be admin-gated and range-validated
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test invoking the replay route from a low-role session
