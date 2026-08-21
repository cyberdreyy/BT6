# Q0855: quoteCreatedAt is a client cursor in resolve-refund-address.ts

## Question
The `after` query is the caller's quoteCreatedAt; can an attacker pass a cursor through resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type that surfaces an older or unrelated order as the user's deposit?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Pass a much earlier cursor and observe the order returned.
- Invariant to test: The polling cursor must be server-issued and bound to the quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a stale cursor to resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert it is refused.
