# Q1641: address rendering truncates the middle in getUserEmbeddedEthereumWallet.ts

## Question
formatWalletAddress shows five leading and four trailing characters; can an attacker generate an address that renders identically to the victim's expected address so a confirmation screen fed by getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 shows the wrong destination as correct?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Grind an address sharing the displayed prefix and suffix and compare renderings.
- Invariant to test: Confirmation surfaces must show enough of the address to be unambiguous.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert two distinct addresses never share a getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 rendering.
