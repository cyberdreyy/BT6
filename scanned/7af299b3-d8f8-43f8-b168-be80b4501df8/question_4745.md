# Q4745: run input reaches the reported value in bridge_types_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) supply request data through `Create` at POST/PATCH/GET /v2/bridge_types that flows into the value the job reports on-chain rather than being confined to metadata?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `Create`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: confirmations and minimum contract payment (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `confirmations and minimum contract payment` with crafted pipeline input/meta.
- Invariant to test: externally supplied run input must not determine the reported observation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: pipeline test asserting the reported value is independent of caller-supplied input
