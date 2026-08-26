# Q4226: payload echoed unvalidated into the response in response_cache.isCacheableStatusCode

## Question
Does `isCacheableStatusCode` at the gateway response cache serving repeated user trigger requests echo attacker-supplied request fields into the user response or into a signed artifact, letting any internet client with an arbitrary externally-owned key sending signed gateway requests shape data another party trusts?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/response_cache.go](core/services/gateway/handlers/capabilities/v2/response_cache.go) -> `isCacheableStatusCode`
- Entrypoint: the gateway response cache serving repeated user trigger requests
- Attacker controls: status codes returned by the DON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `status codes returned by the DON` with crafted echoable fields.
- Invariant to test: responses must contain only server/DON-derived values
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test asserting no request field is echoed into signed output
