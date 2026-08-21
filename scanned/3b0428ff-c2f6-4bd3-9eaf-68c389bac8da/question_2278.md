# Q2278: read_only flag is the only authorization gate in signMessage.ts

## Question
sendCrossAppRequest rejects only when the connection is marked read_only; can an attacker influence the connections response so crossApp signMessage: params [message treats a read-only connection as transactable?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Return a connection without the read_only flag.
- Invariant to test: Transaction authority must be established server-side, not by a client-visible flag.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit read_only in crossApp signMessage: params [message's stub and assert the SDK still requires explicit authority.
