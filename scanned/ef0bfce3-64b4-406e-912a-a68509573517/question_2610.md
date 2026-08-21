# Q2610: domain fields silently dropped in sendTransaction.ts

## Question
generateDomainType keeps only name, version, chainId, verifyingContract and salt; can an attacker include an extra domain field through crossApp sendTransaction: params [transaction] that is dropped from the type list but retained in the domain object, changing the hash?

## Target
- File/function: [src/action/crossApp/wallet/sendTransaction.ts](src/action/crossApp/wallet/sendTransaction.ts) - crossApp sendTransaction: params [transaction], method privy_sendSmartWalletTx or eth_sendTransaction
- Entrypoint: privy.crossApp.wallet.sendTransaction({user, transaction, address, redirectUrl})
- Attacker controls: the transaction object (to, value, data, chainId) and the returned transactionHash
- Exploit idea: Submit a domain with an unknown extra key.
- Invariant to test: Domain and type list must be consistent or the request rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit an extra domain key to crossApp sendTransaction: params [transaction] and assert rejection.
