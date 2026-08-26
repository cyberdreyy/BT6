# Q0431: spec fields reach command or process execution in workflow_keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role submit a spec/parameter through `Index` at GET /v2/keys/workflow whose fields are passed to a process, plugin loader, template or shell, achieving execution on the node host?

## Target
- File/function: [core/web/workflow_keys_controller.go](core/web/workflow_keys_controller.go) -> `Index`
- Entrypoint: GET /v2/keys/workflow
- Attacker controls: selected response fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `selected response fields` with command/path/plugin fields.
- Invariant to test: no user-supplied spec field may reach process, loader or template execution
- Expected Immunefi impact: Critical - arbitrary system command execution on the node host
- Fast validation: unit test asserting spec fields are never interpolated into exec/loader arguments
