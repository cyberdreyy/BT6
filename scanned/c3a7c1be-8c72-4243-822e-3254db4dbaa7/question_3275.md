# Q3275: cluster name switches the mint in resolve-refund-address.ts

## Question
getSolanaUsdcMintAddressForCluster returns a different mint per cluster name; can an attacker pass a cluster name through resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type that yields the devnet mint while the transfer executes on mainnet?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Pass devnet while the transfer targets mainnet.
- Invariant to test: Cluster identity must be consistent across the whole funding flow.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross cluster names in resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert consistency.
