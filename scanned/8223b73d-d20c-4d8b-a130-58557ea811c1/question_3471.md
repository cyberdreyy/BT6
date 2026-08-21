# Q3471: rpc errors collapse to null in unified-wallet.ts

## Question
SolanaClient.getBalance/getAccountInfo/getTokenAccountsByOwner return null on any error; can an attacker cause isUnifiedWallet (account.id && recovery_method === 'privy-v2') to report null so the app treats a funded account as empty (or the reverse) and routes a transfer incorrectly?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Return malformed RPC responses and observe the null results being consumed.
- Invariant to test: Failed reads must be distinguishable from zero-valued reads.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return an RPC error from isUnifiedWallet (account.id && recovery_method === 'privy-v2') and assert the caller receives an error, not null.
