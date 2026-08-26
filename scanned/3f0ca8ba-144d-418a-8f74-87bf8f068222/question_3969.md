# Q3969: shard selection redirects execution in handler.Callback

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests influence the shard/DON chosen by `Callback` at the gateway handler interface boundary every public user request passes through so their request executes on a shard that does not enforce the same authorization?

## Target
- File/function: [core/services/gateway/handlers/handler.go](core/services/gateway/handlers/handler.go) -> `Callback`
- Entrypoint: the gateway handler interface boundary every public user request passes through
- Attacker controls: callback correlation fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `callback correlation fields` with crafted shard-selecting fields.
- Invariant to test: shard selection must not alter the authorization decision
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting identical authorization across shards
