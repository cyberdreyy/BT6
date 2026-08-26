# Q1870: shard selection redirects execution in handler.UserCallbackPayload

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests influence the shard/DON chosen by `UserCallbackPayload` at the gateway handler interface boundary every public user request passes through so their request executes on a shard that does not enforce the same authorization?

## Target
- File/function: [core/services/gateway/handlers/handler.go](core/services/gateway/handlers/handler.go) -> `UserCallbackPayload`
- Entrypoint: the gateway handler interface boundary every public user request passes through
- Attacker controls: request repetition (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request repetition` with crafted shard-selecting fields.
- Invariant to test: shard selection must not alter the authorization decision
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting identical authorization across shards
