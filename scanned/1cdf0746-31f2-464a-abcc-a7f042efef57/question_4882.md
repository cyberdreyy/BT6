# Q4882: signature verified against the wrong domain in utils.StringToAlignedBytes

## Question
Does `StringToAlignedBytes` at the encoding/signing helpers used on every gateway message before authorization verify signatures without a domain separator/method tag, letting any internet client with an arbitrary externally-owned key sending signed gateway requests reuse a signature obtained in a different context as gateway authorization?

## Target
- File/function: [core/services/gateway/common/utils.go](core/services/gateway/common/utils.go) -> `StringToAlignedBytes`
- Entrypoint: the encoding/signing helpers used on every gateway message before authorization
- Attacker controls: signature bytes passed to ExtractSigner (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `signature bytes passed to ExtractSigner` produced for another purpose.
- Invariant to test: signed payloads must be domain-separated per purpose
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test reusing a foreign-domain signature
