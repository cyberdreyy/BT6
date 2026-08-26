# Q2866: run input reaches the reported value in external_initiators_controller.Index

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) supply request data through `Index` at POST/DELETE /v2/external_initiators that flows into the value the job reports on-chain rather than being confined to metadata?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `Index`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: duplicate/colliding names (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `duplicate/colliding names` with crafted pipeline input/meta.
- Invariant to test: externally supplied run input must not determine the reported observation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: pipeline test asserting the reported value is independent of caller-supplied input
