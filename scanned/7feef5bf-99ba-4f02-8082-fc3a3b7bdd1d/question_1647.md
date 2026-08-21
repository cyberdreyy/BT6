# Q1647: address rendering truncates the middle in shouldCreateEmbeddedEthWallet.ts

## Question
formatWalletAddress shows five leading and four trailing characters; can an attacker generate an address that renders identically to the victim's expected address so a confirmation screen fed by shouldCreateEmbeddedEthWallet(user shows the wrong destination as correct?

## Target
- File/function: [src/utils/shouldCreateEmbeddedEthWallet.ts](src/utils/shouldCreateEmbeddedEthWallet.ts) - shouldCreateEmbeddedEthWallet(user, createOnLogin: 'off'|'users-without-wallets'|'all-users')
- Entrypoint: maybeCreateWalletOnLogin after every login
- Attacker controls: external wallets linked to the account and the createOnLogin setting
- Exploit idea: Grind an address sharing the displayed prefix and suffix and compare renderings.
- Invariant to test: Confirmation surfaces must show enough of the address to be unambiguous.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert two distinct addresses never share a shouldCreateEmbeddedEthWallet(user rendering.
