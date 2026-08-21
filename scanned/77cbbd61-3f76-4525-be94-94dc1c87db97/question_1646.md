# Q1646: address rendering truncates the middle in getUserSmartWallet.ts

## Question
formatWalletAddress shows five leading and four trailing characters; can an attacker generate an address that renders identically to the victim's expected address so a confirmation screen fed by getUserSmartWallet: first linked account of type smart_wallet shows the wrong destination as correct?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Grind an address sharing the displayed prefix and suffix and compare renderings.
- Invariant to test: Confirmation surfaces must show enough of the address to be unambiguous.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert two distinct addresses never share a getUserSmartWallet: first linked account of type smart_wallet rendering.
