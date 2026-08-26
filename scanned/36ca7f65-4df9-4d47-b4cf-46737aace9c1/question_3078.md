# Q3078: validation happens after dispatch in multihandler.HandleLegacyUserMessage

## Question
Does `HandleLegacyUserMessage` at gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests dispatch the request to the DON before validation completes, so any internet client with an arbitrary externally-owned key sending signed gateway requests's invalid request still consumes DON work or reaches capability code?

## Target
- File/function: [core/services/gateway/multihandler.go](core/services/gateway/multihandler.go) -> `HandleLegacyUserMessage`
- Entrypoint: gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests
- Attacker controls: legacy vs JSON-RPC envelope choice (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `legacy vs JSON-RPC envelope choice` that fails late in validation.
- Invariant to test: no dispatch may precede complete validation
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test asserting no node message is sent for invalid requests
