# Q3360: solana RPC endpoint chosen by the caller in generateDomainType.ts

## Question
getSolanaRpcEndpointForCluster returns the caller's rpcUrl when present; can an attacker supply a cluster object through generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) so balances and mint data come from an endpoint they control and drive a wrong funding decision?

## Target
- File/function: [src/utils/typedData/generateDomainType.ts](src/utils/typedData/generateDomainType.ts) - generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt)
- Entrypoint: cross-app privy.crossApp.wallet.signTypedData({typedData, ...})
- Attacker controls: the typedData.domain and typedData.types objects
- Exploit idea: Pass a cluster with an attacker RPC URL and observe the reads.
- Invariant to test: RPC endpoints used for value decisions must be trusted and pinned.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a crafted cluster to generateDomainType: rebuilds EIP712Domain from present domain keys (name/version/chainId/verifyingContract/salt) and assert the pinned endpoint is used.
