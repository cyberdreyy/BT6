# Q5050: sender field trusted over recovered signer in message.ExtractSigner

## Question
Does code reached from `ExtractSigner` at the signed gateway message envelope submitted to the public user endpoint use the self-declared sender field rather than the address recovered from the signature, letting any internet client with an arbitrary externally-owned key sending signed gateway requests impersonate another gateway user?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `ExtractSigner`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: the signature bytes (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `signature bytes` with a sender field naming a victim and a valid attacker signature.
- Invariant to test: the acting identity must be the recovered signer only
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: unit test asserting sender is overwritten by the recovered address
