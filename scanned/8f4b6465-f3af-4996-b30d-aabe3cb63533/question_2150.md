# Q2150: meta/data blob echoed into signed output in orm.transact

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) place attacker data in a bridge/initiator meta field through `transact` at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs that reaches the value the node signs and reports on-chain?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `transact`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: concurrent create/update requests (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `concurrent create/update requests` with crafted meta content.
- Invariant to test: externally supplied meta must not influence the reported observation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: pipeline test asserting the report is independent of meta content
