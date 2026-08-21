# Q1644: address rendering truncates the middle in getAllUserEmbeddedSolanaWallets.ts

## Question
formatWalletAddress shows five leading and four trailing characters; can an attacker generate an address that renders identically to the victim's expected address so a confirmation screen fed by getAllUserEmbeddedSolanaWallets: filter embedded + solana shows the wrong destination as correct?

## Target
- File/function: [src/utils/getAllUserEmbeddedSolanaWallets.ts](src/utils/getAllUserEmbeddedSolanaWallets.ts) - getAllUserEmbeddedSolanaWallets: filter embedded + solana, sort by wallet_index
- Entrypoint: Solana wallet enumeration
- Attacker controls: linked_accounts contents, duplicate indices
- Exploit idea: Grind an address sharing the displayed prefix and suffix and compare renderings.
- Invariant to test: Confirmation surfaces must show enough of the address to be unambiguous.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert two distinct addresses never share a getAllUserEmbeddedSolanaWallets: filter embedded + solana rendering.
