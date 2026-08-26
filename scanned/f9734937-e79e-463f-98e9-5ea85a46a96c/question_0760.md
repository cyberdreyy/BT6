# Q0760: resource type confusion in job.NewDirectRequestSpec

## Question
Can an authenticated node user holding only the 'view' role cause `NewDirectRequestSpec` at the JSON:API response of GET /v2/jobs and /v2/jobs/:ID to render one resource type with another's attribute set, exposing fields the intended presenter would redact?

## Target
- File/function: [core/web/presenters/job.go](core/web/presenters/job.go) -> `NewDirectRequestSpec`
- Entrypoint: the JSON:API response of GET /v2/jobs and /v2/jobs/:ID
- Attacker controls: the job id requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `job id requested` with a mismatched type/id.
- Invariant to test: the presenter selected must match the object type exactly
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over presenter selection for mismatched types
