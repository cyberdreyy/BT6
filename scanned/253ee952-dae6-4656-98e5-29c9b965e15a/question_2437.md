# Q2437: legacy and JSON-RPC envelopes disagree in multihandler.HandleLegacyUserMessage

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit the same logical request in the alternate envelope form at gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests so `HandleLegacyUserMessage` applies weaker validation or a different identity?

## Target
- File/function: [core/services/gateway/multihandler.go](core/services/gateway/multihandler.go) -> `HandleLegacyUserMessage`
- Entrypoint: gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests
- Attacker controls: donId selection (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `donId selection` in both envelope forms.
- Invariant to test: both envelope forms must converge on identical validation and identity
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test across the two envelope paths
