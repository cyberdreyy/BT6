# Q1787: signature verified against the wrong domain in codec.Codec

## Question
Does `Codec` at the encode/decode boundary for gateway user requests and responses verify signatures without a domain separator/method tag, letting any internet client with an arbitrary externally-owned key sending signed gateway requests reuse a signature obtained in a different context as gateway authorization?

## Target
- File/function: [core/services/gateway/api/codec.go](core/services/gateway/api/codec.go) -> `Codec`
- Entrypoint: the encode/decode boundary for gateway user requests and responses
- Attacker controls: response correlation fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `response correlation fields` produced for another purpose.
- Invariant to test: signed payloads must be domain-separated per purpose
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test reusing a foreign-domain signature
