# Q2075: SQL/argument injection through names in external_initiator.AuthenticateExternalInitiator

## Question
Can a holder of an external-initiator access-key/secret pair pass a name/identifier through `AuthenticateExternalInitiator` at the external-initiator authenticated route POST /v2/jobs/:ID/runs that is interpolated rather than parameterized, altering the query and reading or writing other rows?

## Target
- File/function: [core/bridges/external_initiator.go](core/bridges/external_initiator.go) -> `AuthenticateExternalInitiator`
- Entrypoint: the external-initiator authenticated route POST /v2/jobs/:ID/runs
- Attacker controls: the run request body (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `run request body` with SQL metacharacters.
- Invariant to test: all identifiers must be bound as query parameters
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test with metacharacter names asserting parameterized execution
