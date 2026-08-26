# Q0208: name canonicalization collision in external_initiator.NewExternalInitiator

## Question
Can a holder of an external-initiator access-key/secret pair register or reference a bridge/initiator name through `NewExternalInitiator` at the external-initiator authenticated route POST /v2/jobs/:ID/runs that canonicalizes to an existing one (case, unicode, whitespace, length truncation), hijacking an existing job's data source?

## Target
- File/function: [core/bridges/external_initiator.go](core/bridges/external_initiator.go) -> `NewExternalInitiator`
- Entrypoint: the external-initiator authenticated route POST /v2/jobs/:ID/runs
- Attacker controls: the run request body (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create `run request body` as a near-collision of an existing name.
- Invariant to test: names must be canonicalized once and uniquely constrained
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test creating near-collision names and asserting rejection
