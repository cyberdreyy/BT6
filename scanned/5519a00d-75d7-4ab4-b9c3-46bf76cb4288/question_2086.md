# Q2086: signature malleability accepted in multihandler.HandleLegacyUserMessage

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present a malleable or alternative-encoding signature at gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests that `HandleLegacyUserMessage` accepts, producing a second valid form of an existing request (replay under a new id)?

## Target
- File/function: [core/services/gateway/multihandler.go](core/services/gateway/multihandler.go) -> `HandleLegacyUserMessage`
- Entrypoint: gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests
- Attacker controls: legacy vs JSON-RPC envelope choice (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `legacy vs JSON-RPC envelope choice` with high-S/alternate v/padded r-s values.
- Invariant to test: signature encoding must be canonical and single-valued
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over ExtractSigner with malleable signatures
