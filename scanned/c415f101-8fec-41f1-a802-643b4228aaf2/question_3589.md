# Q3589: sender field trusted over recovered signer in connectionmanager.DONConnectionManager

## Question
Does code reached from `DONConnectionManager` at the gateway node-facing handshake and connection registry as observed from a user request use the self-declared sender field rather than the address recovered from the signature, letting any internet client with an arbitrary externally-owned key sending signed gateway requests impersonate another gateway user?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `DONConnectionManager`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: handshake timing and repetition (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `handshake timing and repetition` with a sender field naming a victim and a valid attacker signature.
- Invariant to test: the acting identity must be the recovered signer only
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: unit test asserting sender is overwritten by the recovered address
