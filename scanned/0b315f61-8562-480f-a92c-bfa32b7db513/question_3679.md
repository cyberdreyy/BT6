# Q3679: add() skips the access token check in server mode in resolve.ts

## Question
In user-controlled-server-wallets-only mode, add() creates through the wallet-api without the local access-token guard the other branch applies; can an attacker use resolveCrypto: digest and randomUUID defaults from globalThis.crypto to add a wallet without a live session?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Set the config mode and call add with no token present.
- Invariant to test: Every wallet-creating branch must require an authenticated session.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: clear tokens, set server mode and assert resolveCrypto: digest and randomUUID defaults from globalThis.crypto refuses.
