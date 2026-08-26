# Q4479: legacy path skips new validation in aggregator.signedResponseRequestIDEnabled

## Question
Does the legacy message path in `signedResponseRequestIDEnabled` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user skip validation added on the JSON-RPC path, letting any internet client with an arbitrary externally-owned key sending signed gateway requests reach capability code with an under-validated request?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `signedResponseRequestIDEnabled`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: the request fields that derive the signed request id (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request fields that derive the signed request id` through the legacy envelope.
- Invariant to test: both paths must apply identical validation
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test across legacy and JSON-RPC paths
