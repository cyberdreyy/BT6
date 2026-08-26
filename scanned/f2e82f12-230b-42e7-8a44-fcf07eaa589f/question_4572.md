# Q4572: spec fields reach command or process execution in evm_transfer_controller.CreateEVMLegacy

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) submit a spec/parameter through `CreateEVMLegacy` at POST /v2/transfers/evm whose fields are passed to a process, plugin loader, template or shell, achieving execution on the node host?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateEVMLegacy`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: amount and allowHigherAmounts flag (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `amount and allowHigherAmounts flag` with command/path/plugin fields.
- Invariant to test: no user-supplied spec field may reach process, loader or template execution
- Expected Immunefi impact: Critical - arbitrary system command execution on the node host
- Fast validation: unit test asserting spec fields are never interpolated into exec/loader arguments
