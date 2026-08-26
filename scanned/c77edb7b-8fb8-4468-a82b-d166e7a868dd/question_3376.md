# Q3376: plugin sub-path proxying in pipeline_runs_controller.Show

## Question
Can an authenticated node user holding only the 'run' role reach an unintended plugin endpoint through the path segment handled by `Show` at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential), obtaining plugin debug data, memory dumps or command lines with secrets?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Show`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: the resume payload and run id (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `resume payload and run id` with crafted plugin name and sub-path.
- Invariant to test: plugin routes must expose only an explicit allowlist of endpoints, admin-gated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test enumerating plugin sub-paths from a low-role session
