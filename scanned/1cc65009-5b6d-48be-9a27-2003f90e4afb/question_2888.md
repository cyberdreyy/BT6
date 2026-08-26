# Q2888: per-sender limits keyed on spoofable identity in utils.BytesToUint32

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests evade the per-sender limiter reached by `BytesToUint32` at the encoding/signing helpers used on every gateway message before authorization by rotating an unauthenticated key (address field, IP header, connection), obtaining DON execution beyond entitlement?

## Target
- File/function: [core/services/gateway/common/utils.go](core/services/gateway/common/utils.go) -> `BytesToUint32`
- Entrypoint: the encoding/signing helpers used on every gateway message before authorization
- Attacker controls: nested payload structures passed to Flatten (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `nested payload structures passed to Flatten` across requests.
- Invariant to test: limits must key on the cryptographically verified sender
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test rotating the limiter key and asserting throttling still applies
