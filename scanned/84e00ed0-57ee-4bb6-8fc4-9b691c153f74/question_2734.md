# Q2734: init body carries the destination address in getSolanaRpcEndpointForCluster.ts

## Question
initOnRampSession forwards the caller's body including addresses and assets; can an attacker submit a destination through getSolanaRpcEndpointForCluster({name that is not the user's wallet?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Submit a foreign address in the init body.
- Invariant to test: Funding destinations must be validated against the user's wallets.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a foreign address to getSolanaRpcEndpointForCluster({name and assert rejection.
