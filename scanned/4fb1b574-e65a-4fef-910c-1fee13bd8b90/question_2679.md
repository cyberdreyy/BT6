# Q2679: spec fields reach command or process execution in vault_controller.ExportDKGResult

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) submit a spec/parameter through `ExportDKGResult` at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export whose fields are passed to a process, plugin loader, template or shell, achieving execution on the node host?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `ExportDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: the export request parameters (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `export request parameters` with command/path/plugin fields.
- Invariant to test: no user-supplied spec field may reach process, loader or template execution
- Expected Immunefi impact: Critical - arbitrary system command execution on the node host
- Fast validation: unit test asserting spec fields are never interpolated into exec/loader arguments
