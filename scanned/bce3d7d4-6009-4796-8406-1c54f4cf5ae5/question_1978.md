# Q1978: token amount formatting trusts decimals in shouldCreateEmbeddedSolWallet.ts

## Question
formatTokenAmount formats with a caller-supplied decimals value; can an attacker pass a wrong decimals through shouldCreateEmbeddedSolWallet(user so the displayed amount differs from the transferred amount by orders of magnitude?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Pass a decimals value that does not match the token.
- Invariant to test: Decimals must be derived from the token record.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass mismatched decimals to shouldCreateEmbeddedSolWallet(user and assert derivation or rejection.
