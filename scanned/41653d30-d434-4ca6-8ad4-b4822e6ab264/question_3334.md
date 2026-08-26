# Q3334: hex/address normalization differences in multihandler.HandleLegacyUserMessage

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present an address or identifier at gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests in a casing/encoding variant that `HandleLegacyUserMessage` treats as distinct from the allowlisted form, bypassing an authorization or quota keyed on the other form?

## Target
- File/function: [core/services/gateway/multihandler.go](core/services/gateway/multihandler.go) -> `HandleLegacyUserMessage`
- Entrypoint: gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests
- Attacker controls: the requested method name (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `requested method name` in mixed case, checksummed, zero-padded or 0x-less form.
- Invariant to test: identifiers must be canonicalized once before any authorization or accounting decision
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: table test over normalization for all encodings of one identity
