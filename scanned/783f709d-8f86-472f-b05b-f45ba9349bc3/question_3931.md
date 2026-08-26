# Q3931: password hash parameters or algorithm downgrade in orm.FindUser

## Question
Can an unauthenticated HTTP client that can reach the node API port cause the verification in `FindUser` at POST /sessions, API-token auth headers and session cookie lookup to accept a hash produced with a weaker algorithm/cost stored in the record, enabling offline recovery of an admin password?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `FindUser`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: access key/secret pair (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare verification behaviour for `access key/secret pair` across stored hash formats.
- Invariant to test: only the current algorithm and cost may be accepted for verification
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the verifier with legacy hash formats
