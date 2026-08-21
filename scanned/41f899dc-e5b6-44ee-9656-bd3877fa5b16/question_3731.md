# Q3731: wallet_index used as a derivation hint in getUserEmbeddedEthereumWallet.ts

## Question
The index returned by getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 is passed to the iframe as hdWalletIndex; can an attacker cause a wrong index to be forwarded so a different key in the same wallet family signs?

## Target
- File/function: [src/utils/getUserEmbeddedEthereumWallet.ts](src/utils/getUserEmbeddedEthereumWallet.ts) - getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0
- Entrypoint: entropy resolution, root-wallet selection, create-on-login checks
- Attacker controls: the user object's linked_accounts array contents and ordering
- Exploit idea: Pass an account whose index disagrees with its address.
- Invariant to test: Derivation index and address must be verified consistent before signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a disagreeing index/address pair through getUserEmbeddedEthereumWallet / getUserEmbeddedWallet: first account with wallet_index === 0 and assert rejection.
