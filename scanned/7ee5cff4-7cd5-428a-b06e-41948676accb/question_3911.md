# Q3911: revoked workflow still executable in response_cache.isCacheableStatusCode

## Question
Does a workflow deleted, paused or de-authorized upstream remain executable through `isCacheableStatusCode` at the gateway response cache serving repeated user trigger requests until a cache expires, letting any internet client with an arbitrary externally-owned key sending signed gateway requests keep triggering it?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/response_cache.go](core/services/gateway/handlers/capabilities/v2/response_cache.go) -> `isCacheableStatusCode`
- Entrypoint: the gateway response cache serving repeated user trigger requests
- Attacker controls: the cache key fields of the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `cache key fields of the request` after revocation.
- Invariant to test: revocation must take effect before the next accepted trigger
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: integration test triggering after revocation
