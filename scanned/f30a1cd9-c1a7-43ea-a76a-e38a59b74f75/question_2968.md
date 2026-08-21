# Q2968: no ownership assertion in the helper in shouldCreateEmbeddedSolWallet.ts

## Question
shouldCreateEmbeddedSolWallet(user filters the supplied user object without asserting the object came from an authenticated read; can an attacker pass a fabricated user so the helper returns an account they control?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Pass a hand-built user object.
- Invariant to test: Helpers that select signing accounts must require server-confirmed input.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a fabricated user to shouldCreateEmbeddedSolWallet(user and assert the caller re-validates.
