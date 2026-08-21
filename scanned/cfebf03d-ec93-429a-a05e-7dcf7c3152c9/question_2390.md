# Q2390: message parameter order differs by method in sendTransaction.ts

## Question
crossApp signMessage sends params [message, address] while signTypedData sends [address, typedData]; can an attacker exploit an ordering mismatch through crossApp sendTransaction: params [transaction] so the provider signs with the wrong account or over the wrong data?

## Target
- File/function: [src/action/crossApp/wallet/sendTransaction.ts](src/action/crossApp/wallet/sendTransaction.ts) - crossApp sendTransaction: params [transaction], method privy_sendSmartWalletTx or eth_sendTransaction
- Entrypoint: privy.crossApp.wallet.sendTransaction({user, transaction, address, redirectUrl})
- Attacker controls: the transaction object (to, value, data, chainId) and the returned transactionHash
- Exploit idea: Submit requests where message and address are both address-shaped strings.
- Invariant to test: Parameter binding must be explicit and type-checked.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit ambiguous params through crossApp sendTransaction: params [transaction] and assert explicit binding.
