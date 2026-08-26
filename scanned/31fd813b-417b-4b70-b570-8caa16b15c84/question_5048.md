# Q5048: sender field trusted over recovered signer in wsconnection.ReadChannel

## Question
Does code reached from `ReadChannel` at an established gateway WebSocket connection use the self-declared sender field rather than the address recovered from the signature, letting any internet client with an arbitrary externally-owned key sending signed gateway requests impersonate another gateway user?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `ReadChannel`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: connection reset/reconnect timing (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `connection reset/reconnect timing` with a sender field naming a victim and a valid attacker signature.
- Invariant to test: the acting identity must be the recovered signer only
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: unit test asserting sender is overwritten by the recovered address
