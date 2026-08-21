# Q2081: lamports formatting fixed at nine in getUserEmbeddedEthereumWallet.ts

## Question
formatLamportsAmount always divides by 1e9; can an attacker exploit that assumption through getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 for a token that is not SOL so the displayed value is wrong?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Format a non-SOL amount through the lamports path.
- Invariant to test: Unit conversion must be tied to the asset being displayed.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 rejects non-SOL inputs.
