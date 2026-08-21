# Q2283: read_only flag is the only authorization gate in index.ts

## Question
sendCrossAppRequest rejects only when the connection is marked read_only; can an attacker influence the connections response so crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest treats a read-only connection as transactable?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Return a connection without the read_only flag.
- Invariant to test: Transaction authority must be established server-side, not by a client-visible flag.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit read_only in crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest's stub and assert the SDK still requires explicit authority.
