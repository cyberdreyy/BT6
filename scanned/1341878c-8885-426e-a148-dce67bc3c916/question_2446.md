# Q2446: legacy path skips new validation in response_cache.newResponseCache

## Question
Does the legacy message path in `newResponseCache` at the gateway response cache serving repeated user trigger requests skip validation added on the JSON-RPC path, letting any internet client with an arbitrary externally-owned key sending signed gateway requests reach capability code with an under-validated request?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/response_cache.go](core/services/gateway/handlers/capabilities/v2/response_cache.go) -> `newResponseCache`
- Entrypoint: the gateway response cache serving repeated user trigger requests
- Attacker controls: repeat timing versus expiry (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeat timing versus expiry` through the legacy envelope.
- Invariant to test: both paths must apply identical validation
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test across legacy and JSON-RPC paths
