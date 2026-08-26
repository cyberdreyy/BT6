# Q0852: connection identity rebinding in multihandler.NewMultiHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests open a connection through `NewMultiHandler` at gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests and then act under an identity established by another connection because the registry is keyed loosely?

## Target
- File/function: [core/services/gateway/multihandler.go](core/services/gateway/multihandler.go) -> `NewMultiHandler`
- Entrypoint: gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests
- Attacker controls: donId selection (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Open concurrent connections with `donId selection` claiming the same identity.
- Invariant to test: each connection must carry its own verified identity for its lifetime
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: concurrency test asserting per-connection identity isolation
