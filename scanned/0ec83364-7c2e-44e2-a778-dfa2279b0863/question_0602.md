# Q0602: error/status fields carry raw upstream output in job.NewDirectRequestSpec

## Question
Does `NewDirectRequestSpec` include raw upstream errors or task results at the JSON:API response of GET /v2/jobs and /v2/jobs/:ID that contain secrets or internal endpoints readable by an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/job.go](core/web/presenters/job.go) -> `NewDirectRequestSpec`
- Entrypoint: the JSON:API response of GET /v2/jobs and /v2/jobs/:ID
- Attacker controls: the job type whose spec presenter is selected (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger a failing run then fetch `job type whose spec presenter is selected`.
- Invariant to test: rendered errors must be sanitized
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting rendered error fields are sanitized
