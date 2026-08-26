# Q4476: legacy path skips new validation in handler.copiedResponses

## Question
Does the legacy message path in `copiedResponses` at HandleJSONRPCUserMessage on the confidential-relay gateway method skip validation added on the JSON-RPC path, letting any internet client with an arbitrary externally-owned key sending signed gateway requests reach capability code with an under-validated request?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/handler.go](core/services/gateway/handlers/confidentialrelay/handler.go) -> `copiedResponses`
- Entrypoint: HandleJSONRPCUserMessage on the confidential-relay gateway method
- Attacker controls: the relay request payload and method (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `relay request payload and method` through the legacy envelope.
- Invariant to test: both paths must apply identical validation
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test across legacy and JSON-RPC paths
