# Q0854: connection identity rebinding in utils.Uint32ToBytes

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests open a connection through `Uint32ToBytes` at the encoding/signing helpers used on every gateway message before authorization and then act under an identity established by another connection because the registry is keyed loosely?

## Target
- File/function: [core/services/gateway/common/utils.go](core/services/gateway/common/utils.go) -> `Uint32ToBytes`
- Entrypoint: the encoding/signing helpers used on every gateway message before authorization
- Attacker controls: signature bytes passed to ExtractSigner (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Open concurrent connections with `signature bytes passed to ExtractSigner` claiming the same identity.
- Invariant to test: each connection must carry its own verified identity for its lifetime
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: concurrency test asserting per-connection identity isolation
