# Q3935: wallet creation failure hidden in resolve-refund-address.ts

## Question
The refund path returns REFUND_WALLET_CREATION_FAILED from a bare catch; can an attacker force that failure in resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and have the deposit created with a missing or stale refund address?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Fail the create route and inspect the resulting quote body.
- Invariant to test: A deposit must not be created without a valid refund address.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: fail the create route and assert resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type aborts the quote.
