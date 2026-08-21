# Q2088: lamports formatting fixed at nine in shouldCreateEmbeddedSolWallet.ts

## Question
formatLamportsAmount always divides by 1e9; can an attacker exploit that assumption through shouldCreateEmbeddedSolWallet(user for a token that is not SOL so the displayed value is wrong?

## Target
- File/function: [src/utils/shouldCreateEmbeddedSolWallet.ts](src/utils/shouldCreateEmbeddedSolWallet.ts) - shouldCreateEmbeddedSolWallet(user, createOnLogin)
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: linked solana accounts and the createOnLogin setting
- Exploit idea: Format a non-SOL amount through the lamports path.
- Invariant to test: Unit conversion must be tied to the asset being displayed.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert shouldCreateEmbeddedSolWallet(user rejects non-SOL inputs.
