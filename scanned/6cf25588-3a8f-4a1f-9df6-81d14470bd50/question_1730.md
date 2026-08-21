# Q1730: wallet address resolves the provider app in sendTransaction.ts

## Question
getCrossAppAccountByWalletAddress picks the first cross_app account whose embedded_wallets or smart_wallets contains the address; can an attacker cause two accounts to contain the same address so crossApp sendTransaction: params [transaction] routes the request to the wrong provider app?

## Target
- File/function: [src/action/crossApp/wallet/sendTransaction.ts](src/action/crossApp/wallet/sendTransaction.ts) - crossApp sendTransaction: params [transaction], method privy_sendSmartWalletTx or eth_sendTransaction
- Entrypoint: privy.crossApp.wallet.sendTransaction({user, transaction, address, redirectUrl})
- Attacker controls: the transaction object (to, value, data, chainId) and the returned transactionHash
- Exploit idea: Construct a user with duplicate addresses across cross_app accounts.
- Invariant to test: Address to provider resolution must be unique and verified.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build duplicate-address accounts and assert crossApp sendTransaction: params [transaction] refuses to guess.
