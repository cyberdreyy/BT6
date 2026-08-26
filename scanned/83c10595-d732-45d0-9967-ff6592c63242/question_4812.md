# Q4812: meta/data blob echoed into signed output in orm.DeleteBridgeType

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) place attacker data in a bridge/initiator meta field through `DeleteBridgeType` at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs that reaches the value the node signs and reports on-chain?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `DeleteBridgeType`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: external initiator name (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `external initiator name` with crafted meta content.
- Invariant to test: externally supplied meta must not influence the reported observation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: pipeline test asserting the report is independent of meta content
