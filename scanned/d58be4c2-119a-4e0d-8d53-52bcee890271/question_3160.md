# Q3160: provider app id not compared to the account in sendTransaction.ts

## Question
sendCrossAppRequest derives providerAppId from the resolved account, then matches it against the connections list; can an attacker construct state so the two disagree and crossApp sendTransaction: params [transaction] still proceeds?

## Target
- File/function: [src/action/crossApp/wallet/sendTransaction.ts](src/action/crossApp/wallet/sendTransaction.ts) - crossApp sendTransaction: params [transaction], method privy_sendSmartWalletTx or eth_sendTransaction
- Entrypoint: privy.crossApp.wallet.sendTransaction({user, transaction, address, redirectUrl})
- Attacker controls: the transaction object (to, value, data, chainId) and the returned transactionHash
- Exploit idea: Return a connections entry whose provider_app_id matches a different account.
- Invariant to test: Provider identity must be consistent across account and connection.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: create disagreeing state and assert crossApp sendTransaction: params [transaction] refuses.
