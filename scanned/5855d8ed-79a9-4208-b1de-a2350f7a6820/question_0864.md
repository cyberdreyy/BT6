# Q0864: quoteCreatedAt is a client cursor in getSolanaRpcEndpointForCluster.ts

## Question
The `after` query is the caller's quoteCreatedAt; can an attacker pass a cursor through getSolanaRpcEndpointForCluster({name that surfaces an older or unrelated order as the user's deposit?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Pass a much earlier cursor and observe the order returned.
- Invariant to test: The polling cursor must be server-issued and bound to the quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a stale cursor to getSolanaRpcEndpointForCluster({name and assert it is refused.
