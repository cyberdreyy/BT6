# Q0940: quorum/threshold check weak in response_cache.newResponseCache

## Question
Does the aggregation in `newResponseCache` at the gateway response cache serving repeated user trigger requests accept a result below the configured threshold, count duplicates, or ignore mismatched payloads, so any internet client with an arbitrary externally-owned key sending signed gateway requests's crafted request yields an unverified answer?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/response_cache.go](core/services/gateway/handlers/capabilities/v2/response_cache.go) -> `newResponseCache`
- Entrypoint: the gateway response cache serving repeated user trigger requests
- Attacker controls: status codes returned by the DON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `status codes returned by the DON` that triggers the weak branch.
- Invariant to test: results must require distinct, verified contributions meeting the threshold
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test over the aggregator with duplicate/insufficient inputs
