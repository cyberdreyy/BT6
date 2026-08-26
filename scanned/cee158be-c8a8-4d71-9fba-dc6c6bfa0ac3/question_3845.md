# Q3845: metadata sync race grants access in http_handler.HandleNodeMessage

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit at the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest) during the metadata refresh handled by `HandleNodeMessage` so authorization is evaluated against empty or stale metadata and defaults to allow?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_handler.go](core/services/gateway/handlers/capabilities/v2/http_handler.go) -> `HandleNodeMessage`
- Entrypoint: the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest)
- Attacker controls: response routing identifiers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `response routing identifiers` against the sync tick.
- Invariant to test: authorization must fail closed while metadata is unavailable or stale
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test submitting during a metadata gap and asserting rejection
