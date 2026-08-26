# Q4045: first-to-quorum accepts attacker-shaped result in message_util.ValidatedMessageFromReq

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a request at validated conversion between gateway messages, requests and responses so the first aggregator to reach quorum in `ValidatedMessageFromReq` returns a result derived from attacker-controlled input rather than the intended workflow output?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedMessageFromReq`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: encoding variants of identifiers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `encoding variants of identifiers` designed to satisfy the weaker aggregator first.
- Invariant to test: all aggregators must apply identical verification before producing a user response
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test comparing verification across aggregators
