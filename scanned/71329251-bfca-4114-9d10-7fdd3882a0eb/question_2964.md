# Q2964: no ownership assertion in the helper in getAllUserEmbeddedSolanaWallets.ts

## Question
getAllUserEmbeddedSolanaWallets: filter embedded + solana filters the supplied user object without asserting the object came from an authenticated read; can an attacker pass a fabricated user so the helper returns an account they control?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Pass a hand-built user object.
- Invariant to test: Helpers that select signing accounts must require server-confirmed input.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a fabricated user to getAllUserEmbeddedSolanaWallets: filter embedded + solana and assert the caller re-validates.
