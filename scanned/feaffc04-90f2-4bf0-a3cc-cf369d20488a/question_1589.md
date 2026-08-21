# Q1589: first-wallet fallback for entropy in resolve.ts

## Question
getEntropyDetailsFromUser falls back to the first ethereum wallet, then the first solana wallet; can an attacker with multiple linked wallets cause resolveCrypto: digest and randomUUID defaults from globalThis.crypto to derive entropy from a wallet other than the one being signed with?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Sign with a wallet at index 1 and inspect the entropy identity used.
- Invariant to test: Entropy identity must correspond to the exact signing account.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call resolveCrypto: digest and randomUUID defaults from globalThis.crypto with a non-zero wallet_index account and assert the entropy matches that account.
