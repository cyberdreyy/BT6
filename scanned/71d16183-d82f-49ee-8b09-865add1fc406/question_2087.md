# Q2087: lamports formatting fixed at nine in shouldCreateEmbeddedEthWallet.ts

## Question
formatLamportsAmount always divides by 1e9; can an attacker exploit that assumption through shouldCreateEmbeddedEthWallet(user for a token that is not SOL so the displayed value is wrong?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Format a non-SOL amount through the lamports path.
- Invariant to test: Unit conversion must be tied to the asset being displayed.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert shouldCreateEmbeddedEthWallet(user rejects non-SOL inputs.
