# Q5613: secret in relationship/included documents in job.NewCronSpec

## Question
Does the JSON:API relationship or included section produced around `NewCronSpec` at the JSON:API response of GET /v2/jobs and /v2/jobs/:ID carry secret attributes of related objects to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/job.go](core/web/presenters/job.go) -> `NewCronSpec`
- Entrypoint: the JSON:API response of GET /v2/jobs and /v2/jobs/:ID
- Attacker controls: pipeline spec fields returned (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `pipeline spec fields returned` with include parameters.
- Invariant to test: included resources must be redacted like primary resources
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting included documents pass the same redaction
