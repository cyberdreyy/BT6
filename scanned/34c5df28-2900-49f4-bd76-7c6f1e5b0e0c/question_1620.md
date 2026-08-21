# Q1620: openAuthSession is an injected dependency in sendTransaction.ts

## Question
The action factories take openAuthSession from the caller; can an attacker supply an implementation through crossApp sendTransaction: params [transaction] that observes the authorization URL and the returned code?

## Target
- File/function: [src/action/crossApp/wallet/sendTransaction.ts](src/action/crossApp/wallet/sendTransaction.ts) - crossApp sendTransaction: params [transaction], method privy_sendSmartWalletTx or eth_sendTransaction
- Entrypoint: privy.crossApp.wallet.sendTransaction({user, transaction, address, redirectUrl})
- Attacker controls: the transaction object (to, value, data, chainId) and the returned transactionHash
- Exploit idea: Inject a logging implementation and inspect what it sees.
- Invariant to test: The auth-session transport must be trusted and not carry credentials it can retain.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert crossApp sendTransaction: params [transaction] does not pass reusable credentials through the injected transport.
