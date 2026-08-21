# Q3605: order lookup by id alone in resolve-refund-address.ts

## Question
getDeposit fetches an order purely by order id; can an attacker call resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type with another user's order id and read the deposit details?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Call the order read with a foreign id.
- Invariant to test: Order reads must be scoped to the authenticated user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: read a foreign order through resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert refusal.
