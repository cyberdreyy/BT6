# Q2680: spec fields reach command or process execution in evm_transfer_controller.CreateWithRelayer

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) submit a spec/parameter through `CreateWithRelayer` at POST /v2/transfers/evm whose fields are passed to a process, plugin loader, template or shell, achieving execution on the node host?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateWithRelayer`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: gas limit and token contract fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `gas limit and token contract fields` with command/path/plugin fields.
- Invariant to test: no user-supplied spec field may reach process, loader or template execution
- Expected Immunefi impact: Critical - arbitrary system command execution on the node host
- Fast validation: unit test asserting spec fields are never interpolated into exec/loader arguments
