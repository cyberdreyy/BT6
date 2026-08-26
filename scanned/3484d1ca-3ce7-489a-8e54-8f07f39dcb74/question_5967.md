# Q5967: grace window abused to alter the bundle in response_cache.isExpiredOrNotCached

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit during the grace window handled by `isExpiredOrNotCached` at the gateway response cache serving repeated user trigger requests so the bundle returned to the user includes or omits responses chosen by the attacker?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/response_cache.go](core/services/gateway/handlers/capabilities/v2/response_cache.go) -> `isExpiredOrNotCached`
- Entrypoint: the gateway response cache serving repeated user trigger requests
- Attacker controls: status codes returned by the DON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `status codes returned by the DON` against the grace deadline.
- Invariant to test: bundle composition must be determined by verified node responses only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test over bundle composition across grace timings
