# Q0415: destination address unvalidated in resolve-refund-address.ts

## Question
generateDepositAddress forwards destination_address verbatim into the quote body; can an attacker submit a destination through resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type that is not owned by the user, or is on the wrong chain, so funds settle where the user did not intend?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Submit a destination address from a different chain family.
- Invariant to test: The destination must be validated against the destination chain and the user's own accounts.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a cross-chain destination to resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert rejection.
