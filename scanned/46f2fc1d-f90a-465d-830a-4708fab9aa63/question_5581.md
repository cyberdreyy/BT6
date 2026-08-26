# Q5581: response body injected into workflow input in message_util.ValidatedResponseFromMessage

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests shape the response returned through `ValidatedResponseFromMessage` at validated conversion between gateway messages, requests and responses so a workflow consumes attacker-controlled data as trusted input to an on-chain report?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedResponseFromMessage`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: response fields echoed from the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Serve `response fields echoed from the request` from a target the node fetches.
- Invariant to test: externally fetched data must be treated as untrusted at the consumption point
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test asserting fetched data cannot alter the reported value
