# Q4939: empty or absent signature accepted in multihandler.HandleJSONRPCUserMessage

## Question
Does a request with an empty, zero or absent signature at gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests pass through `HandleJSONRPCUserMessage` and receive an identity (zero address) that later checks treat as valid?

## Target
- File/function: [core/services/gateway/multihandler.go](core/services/gateway/multihandler.go) -> `HandleJSONRPCUserMessage`
- Entrypoint: gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests
- Attacker controls: legacy vs JSON-RPC envelope choice (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `legacy vs JSON-RPC envelope choice` without signature material.
- Invariant to test: missing signatures must be rejected before identity assignment
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test with empty/zero signatures
