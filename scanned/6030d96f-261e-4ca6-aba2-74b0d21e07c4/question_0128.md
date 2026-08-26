# Q0128: struct embedding pulls in secret fields in job.NewDirectRequestSpec

## Question
Does `NewDirectRequestSpec` embed a domain struct so newly added secret fields are serialized automatically at the JSON:API response of GET /v2/jobs and /v2/jobs/:ID without anyone reviewing the response shape?

## Target
- File/function: [core/web/presenters/job.go](core/web/presenters/job.go) -> `NewDirectRequestSpec`
- Entrypoint: the JSON:API response of GET /v2/jobs and /v2/jobs/:ID
- Attacker controls: the job type whose spec presenter is selected (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `job type whose spec presenter is selected` and compare fields against the intended resource contract.
- Invariant to test: presenters must copy explicit fields rather than embed domain structs
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting the presenter's field set equals an explicit allowlist
