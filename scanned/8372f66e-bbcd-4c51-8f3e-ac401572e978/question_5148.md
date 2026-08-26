# Q5148: log control abused to capture secrets in pipeline_runs_controller.Create

## Question
Can an authenticated node user holding only the 'run' role change logging behaviour through `Create` at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) (level, SQL logging) so credentials or key material are written where a lower-privilege path can read them?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Create`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: repeated/concurrent submissions (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Toggle `repeated/concurrent submissions` then trigger a credential-bearing request.
- Invariant to test: log configuration changes require admin authority and must not enable secret logging
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test toggling log settings from a low-role session
