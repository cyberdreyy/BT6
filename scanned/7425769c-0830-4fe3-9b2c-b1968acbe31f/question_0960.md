# Q0960: storage key namespaced only by provider app id in sendTransaction.ts

## Question
The cache key is privy:cross-app:<providerAppId>; can an attacker use a providerAppId string through crossApp sendTransaction: params [transaction] that collides with another key namespace or with a different app's entry?

## Target
- File/function: [src/action/crossApp/wallet/sendTransaction.ts](src/action/crossApp/wallet/sendTransaction.ts) - crossApp sendTransaction: params [transaction], method privy_sendSmartWalletTx or eth_sendTransaction
- Entrypoint: privy.crossApp.wallet.sendTransaction({user, transaction, address, redirectUrl})
- Attacker controls: the transaction object (to, value, data, chainId) and the returned transactionHash
- Exploit idea: Pass a providerAppId containing ':' or matching another key prefix.
- Invariant to test: Storage keys must be injective over provider app ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass separator-bearing provider ids to crossApp sendTransaction: params [transaction] and assert distinct keys.
