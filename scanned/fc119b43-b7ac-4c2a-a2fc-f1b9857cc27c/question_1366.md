# Q1366: profiling endpoint yields key material in pipeline_runs_controller.Index

## Question
Can an authenticated node user holding only the 'run' role obtain a heap/goroutine profile through `Index` at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) containing in-memory private keys, passwords or session tokens?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Index`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: the run request body (JSON pipeline input, meta) (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `run request body (JSON pipeline input, meta)` against the profiling handler and scan the dump.
- Invariant to test: profiling endpoints must be admin-only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test fetching a profile from a low-role session and asserting 403
