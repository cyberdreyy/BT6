# Q3581: token account picked with .at(0) in unified-wallet.ts

## Question
getTokenAccountsByOwner takes the first returned account's parsed amount; can an attacker cause multiple token accounts to be returned so isUnifiedWallet (account.id && recovery_method === 'privy-v2') reports a balance from an account the user does not control?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Return several accounts including a zero-balance decoy first.
- Invariant to test: Balance aggregation must consider every matching account.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return multiple accounts from isUnifiedWallet (account.id && recovery_method === 'privy-v2')'s RPC stub and assert correct aggregation.
