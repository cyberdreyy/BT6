# Q3495: deposit config fetched but not enforced in resolve-refund-address.ts

## Question
getConfig returns currencies and chains but the generate path does not consult it; can an attacker submit a quote through resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type for a pair the config excludes?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Submit an excluded pair after fetching the config.
- Invariant to test: The client must enforce the fetched configuration before creating a quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an excluded pair to resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert refusal.
