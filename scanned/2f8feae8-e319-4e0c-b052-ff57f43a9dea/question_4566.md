# Q4566: spec fields reach command or process execution in jobs_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) submit a spec/parameter through `Create` at POST/PATCH /v2/jobs (edit role) whose fields are passed to a process, plugin loader, template or shell, achieving execution on the node host?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Create`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: spec type and pipeline DAG (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `spec type and pipeline DAG` with command/path/plugin fields.
- Invariant to test: no user-supplied spec field may reach process, loader or template execution
- Expected Immunefi impact: Critical - arbitrary system command execution on the node host
- Fast validation: unit test asserting spec fields are never interpolated into exec/loader arguments
