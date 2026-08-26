# Q4234: payload echoed unvalidated into the response in message_util.ValidatedMessageFromReq

## Question
Does `ValidatedMessageFromReq` at validated conversion between gateway messages, requests and responses echo attacker-supplied request fields into the user response or into a signed artifact, letting any internet client with an arbitrary externally-owned key sending signed gateway requests shape data another party trusts?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedMessageFromReq`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: encoding variants of identifiers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `encoding variants of identifiers` with crafted echoable fields.
- Invariant to test: responses must contain only server/DON-derived values
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test asserting no request field is echoed into signed output
