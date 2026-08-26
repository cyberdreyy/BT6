# Q0072: workflow ownership claimed, not proven in shard_endpoints.buildShardEndpoints

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests name another owner's workflow in the fields validated by `buildShardEndpoints` at shard selection for a user trigger request routed to a sharded DON and have the DON execute it, with the work attributed to and paid by that owner?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/shard_endpoints.go](core/services/gateway/handlers/capabilities/v2/shard_endpoints.go) -> `buildShardEndpoints`
- Entrypoint: shard selection for a user trigger request routed to a sharded DON
- Attacker controls: workflow identifiers that select a shard (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `workflow identifiers that select a shard` with the victim's workflow owner/name/tag and the attacker's signature.
- Invariant to test: workflow execution must require proof that the sender is the owner or an authorized caller for that workflow
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test submitting a trigger for a foreign workflow
