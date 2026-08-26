# Q4746: run input reaches the reported value in keys_controller.Delete

## Question
Can an authenticated node user holding only the 'view' role supply request data through `Delete` at /v2/keys/:keyType Index/Export/Import/Delete routes that flows into the value the job reports on-chain rather than being confined to metadata?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Delete`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the imported key JSON and its password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `imported key JSON and its password` with crafted pipeline input/meta.
- Invariant to test: externally supplied run input must not determine the reported observation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: pipeline test asserting the reported value is independent of caller-supplied input
