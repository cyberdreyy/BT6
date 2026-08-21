# Q3362: solana RPC endpoint chosen by the caller in smart-wallets.ts

## Question
getSolanaRpcEndpointForCluster returns the caller's rpcUrl when present; can an attacker supply a cluster object through smart-wallets entry (BICONOMY so balances and mint data come from an endpoint they control and drive a wrong funding decision?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Pass a cluster with an attacker RPC URL and observe the reads.
- Invariant to test: RPC endpoints used for value decisions must be trusted and pinned.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a crafted cluster to smart-wallets entry (BICONOMY and assert the pinned endpoint is used.
