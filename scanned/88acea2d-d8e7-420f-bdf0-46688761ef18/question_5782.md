# Q5782: custom marshaller leaks on error in job.NewVRFSpec

## Question
Does the marshalling path around `NewVRFSpec` fall back to default struct marshalling on error at the JSON:API response of GET /v2/jobs and /v2/jobs/:ID, exposing unredacted fields to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/job.go](core/web/presenters/job.go) -> `NewVRFSpec`
- Entrypoint: the JSON:API response of GET /v2/jobs and /v2/jobs/:ID
- Attacker controls: pipeline spec fields returned (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the error branch via `pipeline spec fields returned`.
- Invariant to test: marshalling failure must produce an error, never a raw dump
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test forcing marshal errors and asserting no raw payload
