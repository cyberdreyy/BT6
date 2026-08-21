# Q0520: timestamp not validated on return in sendTransaction.ts

## Question
The request payload contains Date.now() but nothing verifies it on the way back; can an attacker replay an old cross-app response into crossApp sendTransaction: params [transaction]?

## Target
- File/function: [src/action/crossApp/wallet/sendTransaction.ts](src/action/crossApp/wallet/sendTransaction.ts) - crossApp sendTransaction: params [transaction], method privy_sendSmartWalletTx or eth_sendTransaction
- Entrypoint: privy.crossApp.wallet.sendTransaction({user, transaction, address, redirectUrl})
- Attacker controls: the transaction object (to, value, data, chainId) and the returned transactionHash
- Exploit idea: Capture a response and replay it for a later request.
- Invariant to test: Cross-app responses must be fresh and single-use.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured response into crossApp sendTransaction: params [transaction] and assert rejection.
