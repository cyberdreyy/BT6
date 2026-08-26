# Q1387: token hashing weakness in external_initiator.AuthenticateExternalInitiator

## Question
Is the incoming-token hash computed in `AuthenticateExternalInitiator` from the external-initiator authenticated route POST /v2/jobs/:ID/runs unsalted, truncated or reversible, letting a holder of an external-initiator access-key/secret pair derive an accepted token from stored or leaked material?

## Target
- File/function: [core/bridges/external_initiator.go](core/bridges/external_initiator.go) -> `AuthenticateExternalInitiator`
- Entrypoint: the external-initiator authenticated route POST /v2/jobs/:ID/runs
- Attacker controls: the run request body (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Analyze `run request body` against the stored hash form.
- Invariant to test: tokens must be stored as salted, full-length hashes
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test asserting the hash construction and length
