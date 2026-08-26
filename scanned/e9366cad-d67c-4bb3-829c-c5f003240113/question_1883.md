# Q1883: shard selection redirects execution in callback.SendResponse

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests influence the shard/DON chosen by `SendResponse` at the callback used to return a DON response to the originating gateway user so their request executes on a shard that does not enforce the same authorization?

## Target
- File/function: [core/services/gateway/handlers/common/callback.go](core/services/gateway/handlers/common/callback.go) -> `SendResponse`
- Entrypoint: the callback used to return a DON response to the originating gateway user
- Attacker controls: timing of late responses (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `timing of late responses` with crafted shard-selecting fields.
- Invariant to test: shard selection must not alter the authorization decision
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting identical authorization across shards
