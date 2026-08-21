# Q3361: solana RPC endpoint chosen by the caller in unified-wallet.ts

## Question
getSolanaRpcEndpointForCluster returns the caller's rpcUrl when present; can an attacker supply a cluster object through isUnifiedWallet (account.id && recovery_method === 'privy-v2') so balances and mint data come from an endpoint they control and drive a wrong funding decision?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Pass a cluster with an attacker RPC URL and observe the reads.
- Invariant to test: RPC endpoints used for value decisions must be trusted and pinned.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a crafted cluster to isUnifiedWallet (account.id && recovery_method === 'privy-v2') and assert the pinned endpoint is used.
