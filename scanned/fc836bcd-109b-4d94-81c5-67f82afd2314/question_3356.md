# Q3356: solana RPC endpoint chosen by the caller in isVersionedTransaction.ts

## Question
getSolanaRpcEndpointForCluster returns the caller's rpcUrl when present; can an attacker supply a cluster object through isVersionedTransaction ('version' in tx) so balances and mint data come from an endpoint they control and drive a wrong funding decision?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Pass a cluster with an attacker RPC URL and observe the reads.
- Invariant to test: RPC endpoints used for value decisions must be trusted and pinned.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a crafted cluster to isVersionedTransaction ('version' in tx) and assert the pinned endpoint is used.
