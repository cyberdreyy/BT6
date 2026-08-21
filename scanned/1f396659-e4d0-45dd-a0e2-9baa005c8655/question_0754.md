# Q0754: polling accepts any order for the address in getSolanaRpcEndpointForCluster.ts

## Question
waitForDeposit polls GetNextDepositAddressOrder with a deposit address id and an `after` timestamp, then fetches whatever order id comes back; can an attacker cause getSolanaRpcEndpointForCluster({name to bind to an order that is not the user's deposit?

## Target
- File/function: [src/solana/getSolanaRpcEndpointForCluster.ts](src/solana/getSolanaRpcEndpointForCluster.ts) - getSolanaRpcEndpointForCluster({name, rpcUrl}) - caller rpcUrl wins over the cluster default
- Entrypoint: SolanaClient construction for balance and mint reads
- Attacker controls: the cluster object passed in by the caller
- Exploit idea: Return a next-order response naming a foreign order id.
- Invariant to test: Polled orders must be verified to belong to the requesting deposit and user.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return a foreign order id in getSolanaRpcEndpointForCluster({name's stub and assert it is rejected.
