# Q4108: grace window abused to alter the bundle in message_util.ValidatedMessageFromReq

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit during the grace window handled by `ValidatedMessageFromReq` at validated conversion between gateway messages, requests and responses so the bundle returned to the user includes or omits responses chosen by the attacker?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedMessageFromReq`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: the message body fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `message body fields` against the grace deadline.
- Invariant to test: bundle composition must be determined by verified node responses only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test over bundle composition across grace timings
