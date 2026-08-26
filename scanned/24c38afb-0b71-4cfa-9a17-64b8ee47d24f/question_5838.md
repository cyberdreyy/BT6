# Q5838: redaction applied only on one route in job.NewVRFSpec

## Question
Is redaction in `NewVRFSpec` applied on the index route but not on show/export/create at the JSON:API response of GET /v2/jobs and /v2/jobs/:ID, letting an authenticated node user holding only the 'view' role read the secret through the other route?

## Target
- File/function: [core/web/presenters/job.go](core/web/presenters/job.go) -> `NewVRFSpec`
- Entrypoint: the JSON:API response of GET /v2/jobs and /v2/jobs/:ID
- Attacker controls: the job id requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare `job id requested` across all routes rendering the same resource.
- Invariant to test: redaction must be a property of the resource, not of one route
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test comparing the field set across routes
