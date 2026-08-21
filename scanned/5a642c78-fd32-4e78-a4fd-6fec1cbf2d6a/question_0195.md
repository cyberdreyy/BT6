# Q0195: refund falls back to creating a wallet in resolve-refund-address.ts

## Question
When no matching account exists, resolveRefundAddress creates a wallet via the WalletCreate route and returns its address; can an attacker trigger that path through deposit-address generation without an explicit refundAddress so a fresh wallet is provisioned and used as a refund sink without user confirmation?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Call the deposit flow for a chain the user has no wallet on.
- Invariant to test: Automatic wallet creation must not silently become the refund destination.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: call resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type for an unlinked chain and assert an explicit confirmation is required.
