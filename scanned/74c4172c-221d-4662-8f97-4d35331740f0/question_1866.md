# Q1866: wei formatting strips trailing digits in getUserSmartWallet.ts

## Question
formatWeiAmount fixes to three decimals and strips trailing zeros and dots; can an attacker choose an amount so getUserSmartWallet: first linked account of type smart_wallet displays a materially smaller value than will be signed?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Format values just below the display precision.
- Invariant to test: Displayed amounts must never round down the value being approved.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getUserSmartWallet: first linked account of type smart_wallet never displays less than the true amount.
