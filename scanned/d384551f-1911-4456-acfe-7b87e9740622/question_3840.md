# Q3840: length/alignment helper mismatch in multihandler.HandleJSONRPCUserMessage

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a string/byte field at gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests whose alignment or length handling in `HandleJSONRPCUserMessage` makes the parsed value differ from the signed value?

## Target
- File/function: [core/services/gateway/multihandler.go](core/services/gateway/multihandler.go) -> `HandleJSONRPCUserMessage`
- Entrypoint: gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests
- Attacker controls: legacy vs JSON-RPC envelope choice (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `legacy vs JSON-RPC envelope choice` with padding, embedded NULs or non-UTF8 bytes.
- Invariant to test: encode/decode must round-trip exactly for every input
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: round-trip fuzz-style table test over the alignment helpers
