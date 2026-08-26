# Q0308: length/format validation gap in response_cache.newResponseCache

## Question
Does the field validation in `newResponseCache` at the gateway response cache serving repeated user trigger requests accept an over-long, truncated or non-hex identifier that later code slices or parses unchecked, letting any internet client with an arbitrary externally-owned key sending signed gateway requests address a different workflow?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/response_cache.go](core/services/gateway/handlers/capabilities/v2/response_cache.go) -> `newResponseCache`
- Entrypoint: the gateway response cache serving repeated user trigger requests
- Attacker controls: the cache key fields of the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `cache key fields of the request` at and beyond the documented length bounds.
- Invariant to test: every identifier must be length- and charset-validated before use
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test at length boundaries for each identifier field
