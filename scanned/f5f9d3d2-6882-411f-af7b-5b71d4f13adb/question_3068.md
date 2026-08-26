# Q3068: meta/data blob echoed into signed output in bridge_type.incomingTokenHash

## Question
Can a holder of an external-initiator access-key/secret pair place attacker data in a bridge/initiator meta field through `incomingTokenHash` at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs that reaches the value the node signs and reports on-chain?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `incomingTokenHash`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: the bridge JSON body (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge JSON body` with crafted meta content.
- Invariant to test: externally supplied meta must not influence the reported observation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: pipeline test asserting the report is independent of meta content
