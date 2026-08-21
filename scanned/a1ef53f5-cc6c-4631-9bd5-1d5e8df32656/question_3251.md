# Q3251: disconnect leaves the wrapper usable in unified-wallet.ts

## Question
disconnect only calls the standard feature; can an attacker keep using isUnifiedWallet (account.id && recovery_method === 'privy-v2') after disconnect so signatures are still requested from a wallet the user disconnected?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Call disconnect then sign.
- Invariant to test: A disconnected wallet wrapper must refuse further operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call disconnect then sign through isUnifiedWallet (account.id && recovery_method === 'privy-v2') and assert rejection.
