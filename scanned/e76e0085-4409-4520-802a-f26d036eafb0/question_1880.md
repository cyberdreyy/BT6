# Q1880: shard selection redirects execution in handler.addResponseForNode

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests influence the shard/DON chosen by `addResponseForNode` at the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint so their request executes on a shard that does not enforce the same authorization?

## Target
- File/function: [core/services/gateway/handlers/vault/handler.go](core/services/gateway/handlers/vault/handler.go) -> `addResponseForNode`
- Entrypoint: the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint
- Attacker controls: signature over the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `signature over the request` with crafted shard-selecting fields.
- Invariant to test: shard selection must not alter the authorization decision
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting identical authorization across shards
