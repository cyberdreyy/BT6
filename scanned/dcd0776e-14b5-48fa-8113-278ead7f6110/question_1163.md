# Q1163: per-sender limits keyed on spoofable identity in codec.Codec

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests evade the per-sender limiter reached by `Codec` at the encode/decode boundary for gateway user requests and responses by rotating an unauthenticated key (address field, IP header, connection), obtaining DON execution beyond entitlement?

## Target
- File/function: [core/services/gateway/api/codec.go](core/services/gateway/api/codec.go) -> `Codec`
- Entrypoint: the encode/decode boundary for gateway user requests and responses
- Attacker controls: encoding variants of the same logical request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `encoding variants of the same logical request` across requests.
- Invariant to test: limits must key on the cryptographically verified sender
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test rotating the limiter key and asserting throttling still applies
