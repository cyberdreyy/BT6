# Q2567: challenge reuse or predictability in connectionmanager.buildNodeStates

## Question
Is the challenge produced/validated by `buildNodeStates` at the gateway node-facing handshake and connection registry as observed from a user request predictable, reusable or unbound to the connection, letting any internet client with an arbitrary externally-owned key sending signed gateway requests replay a captured handshake response?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `buildNodeStates`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: the claimed address in the handshake (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `claimed address in the handshake` captured from another handshake.
- Invariant to test: challenges must be random, single-use and connection-bound
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a handshake response
