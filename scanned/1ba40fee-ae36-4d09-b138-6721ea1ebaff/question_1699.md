# Q1699: imported wallets bypass the fallback in resolve.ts

## Question
getEntropyDetailsFromUser returns the signing account directly when imported is set; can an attacker mark an account object as imported so resolveCrypto: digest and randomUUID defaults from globalThis.crypto derives entropy from an account of their choosing?

## Target
- File/function: [src/crypto/resolve.ts](src/crypto/resolve.ts) - resolveCrypto: digest and randomUUID defaults from globalThis.crypto
- Entrypoint: new Privy({crypto})
- Attacker controls: the crypto object the integrator/app passes in, and fallback behaviour when subtle is unavailable
- Exploit idea: Pass a hand-built account with imported true.
- Invariant to test: Account flags used for entropy selection must come from server-confirmed data.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass {imported:true} on a crafted account to resolveCrypto: digest and randomUUID defaults from globalThis.crypto and assert re-validation against the session user.
