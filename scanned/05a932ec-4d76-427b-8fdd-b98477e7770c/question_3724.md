# Q3724: onWalletCreated callback fires before confirmation in getSolanaRpcEndpointForCluster.ts

## Question
resolveRefundAddress invokes onWalletCreated after the create call returns; can an attacker use getSolanaRpcEndpointForCluster({name so the app treats an unconfirmed wallet as ready and routes funds to it?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Return a create response and inspect the callback timing versus session refresh.
- Invariant to test: Callbacks signalling readiness must follow a confirmed session refresh.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert getSolanaRpcEndpointForCluster({name refreshes the user before invoking the callback.
