# Q3464: empty or absent signature accepted in utils.BytesToUint32

## Question
Does a request with an empty, zero or absent signature at the encoding/signing helpers used on every gateway message before authorization pass through `BytesToUint32` and receive an identity (zero address) that later checks treat as valid?

## Target
- File/function: [core/services/gateway/common/utils.go](core/services/gateway/common/utils.go) -> `BytesToUint32`
- Entrypoint: the encoding/signing helpers used on every gateway message before authorization
- Attacker controls: nested payload structures passed to Flatten (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `nested payload structures passed to Flatten` without signature material.
- Invariant to test: missing signatures must be rejected before identity assignment
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test with empty/zero signatures
