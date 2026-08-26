# Q3158: duplicate response overwrites the result in message_util.ValidatedMessageFromReq

## Question
Can a second response accepted by `ValidatedMessageFromReq` at validated conversion between gateway messages, requests and responses overwrite an already-delivered result so any internet client with an arbitrary externally-owned key sending signed gateway requests changes what a workflow consumes?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedMessageFromReq`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: the message body fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force duplicates via `message body fields`.
- Invariant to test: response processing must be single-shot per request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test sending duplicate responses and asserting the first wins
