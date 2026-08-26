# Q4998: signature not covering all authorization fields in utils.AlignedBytesToString

## Question
Does the signature validated on the path through `AlignedBytesToString` at the encoding/signing helpers used on every gateway message before authorization cover every field used for authorization (sender, method, donId, receiver, payload), or can any internet client with an arbitrary externally-owned key sending signed gateway requests mutate an uncovered field after signing?

## Target
- File/function: [core/services/gateway/common/utils.go](core/services/gateway/common/utils.go) -> `AlignedBytesToString`
- Entrypoint: the encoding/signing helpers used on every gateway message before authorization
- Attacker controls: strings and byte lengths that hit alignment/padding helpers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Sign one message, then alter `strings and byte lengths that hit alignment/padding helpers` before sending.
- Invariant to test: the signed digest must commit to every field later used for routing or authorization
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test mutating each field of a signed message and asserting rejection
