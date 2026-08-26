# Q5101: bridge URL replaced under a live job in bridge_type.MustParseBridgeName

## Question
Can a holder of an external-initiator access-key/secret pair update the bridge URL/token through `MustParseBridgeName` at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs so running jobs fetch observations from an attacker endpoint, changing the reported value?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `MustParseBridgeName`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: the bridge JSON body (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Patch `bridge JSON body` to point at an attacker host.
- Invariant to test: bridge target changes must require admin authority and revalidate referencing jobs
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test patching a bridge used by a live job
