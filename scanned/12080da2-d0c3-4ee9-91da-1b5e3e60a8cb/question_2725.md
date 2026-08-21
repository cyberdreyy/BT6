# Q2725: init body carries the destination address in resolve-refund-address.ts

## Question
initOnRampSession forwards the caller's body including addresses and assets; can an attacker submit a destination through resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type that is not the user's wallet?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Submit a foreign address in the init body.
- Invariant to test: Funding destinations must be validated against the user's wallets.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a foreign address to resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert rejection.
