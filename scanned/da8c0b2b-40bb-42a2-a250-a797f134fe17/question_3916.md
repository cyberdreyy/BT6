# Q3916: revoked workflow still executable in aggregator.signedResponseRequestIDEnabled

## Question
Does a workflow deleted, paused or de-authorized upstream remain executable through `signedResponseRequestIDEnabled` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user until a cache expires, letting any internet client with an arbitrary externally-owned key sending signed gateway requests keep triggering it?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `signedResponseRequestIDEnabled`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: the request fields that derive the signed request id (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `request fields that derive the signed request id` after revocation.
- Invariant to test: revocation must take effect before the next accepted trigger
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: integration test triggering after revocation
