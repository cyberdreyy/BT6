# Q3196: initiator not bound to its job in bridge_type.MarshalBridgeMetaData

## Question
Can a holder of an external-initiator access-key/secret pair authenticate with one initiator's credential at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs and, through `MarshalBridgeMetaData`, trigger runs for jobs bound to a different initiator?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `MarshalBridgeMetaData`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: bridge name string (case, unicode, length) (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `bridge name string (case, unicode, length)` against another job's run endpoint.
- Invariant to test: an initiator may only trigger the jobs whose spec names it
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test triggering a foreign job with a valid EI credential
