# Q2452: legacy path skips new validation in requestcache.NewRequest

## Question
Does the legacy message path in `NewRequest` at the gateway request cache keyed per user request skip validation added on the JSON-RPC path, letting any internet client with an arbitrary externally-owned key sending signed gateway requests reach capability code with an under-validated request?

## Target
- File/function: [core/services/gateway/handlers/common/requestcache.go](core/services/gateway/handlers/common/requestcache.go) -> `NewRequest`
- Entrypoint: the gateway request cache keyed per user request
- Attacker controls: repeat and concurrent submissions (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeat and concurrent submissions` through the legacy envelope.
- Invariant to test: both paths must apply identical validation
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test across legacy and JSON-RPC paths
