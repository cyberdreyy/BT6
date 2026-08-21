# Q3725: onWalletCreated callback fires before confirmation in getSolanaUsdcMintAddressForCluster.ts

## Question
resolveRefundAddress invokes onWalletCreated after the create call returns; can an attacker use getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster so the app treats an unconfirmed wallet as ready and routes funds to it?

## Target
- File/function: [src/solana/getSolanaUsdcMintAddressForCluster.ts](src/solana/getSolanaUsdcMintAddressForCluster.ts) - getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster, testnet unsupported
- Entrypoint: USDC funding on Solana
- Attacker controls: the cluster name string
- Exploit idea: Return a create response and inspect the callback timing versus session refresh.
- Invariant to test: Callbacks signalling readiness must follow a confirmed session refresh.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getSolanaUsdcMintAddressForCluster({name}) - throws on unknown cluster refreshes the user before invoking the callback.
