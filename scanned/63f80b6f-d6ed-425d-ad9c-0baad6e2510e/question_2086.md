# Q2086: lamports formatting fixed at nine in getUserSmartWallet.ts

## Question
formatLamportsAmount always divides by 1e9; can an attacker exploit that assumption through getUserSmartWallet: first linked account of type smart_wallet for a token that is not SOL so the displayed value is wrong?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Format a non-SOL amount through the lamports path.
- Invariant to test: Unit conversion must be tied to the asset being displayed.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getUserSmartWallet: first linked account of type smart_wallet rejects non-SOL inputs.
