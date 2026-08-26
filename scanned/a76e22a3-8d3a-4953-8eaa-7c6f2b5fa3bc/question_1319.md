# Q1319: error path echoes internal state in codec.Codec

## Question
Do gateway errors produced near `Codec` at the encode/decode boundary for gateway user requests and responses disclose node addresses, DON membership, internal URLs or key identifiers to any internet client with an arbitrary externally-owned key sending signed gateway requests?

## Target
- File/function: [core/services/gateway/api/codec.go](core/services/gateway/api/codec.go) -> `Codec`
- Entrypoint: the encode/decode boundary for gateway user requests and responses
- Attacker controls: response correlation fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `response correlation fields`.
- Invariant to test: gateway errors must be generic
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting error payloads match an allowlist
