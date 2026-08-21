# Q0190: provider api url comes from the connections list in sendTransaction.ts

## Question
The transact URL host is provider_app_custom_api_url taken from the getCrossAppConnections response; can an attacker influence that value so crossApp sendTransaction: params [transaction] sends the provider access token and the request payload to a host of their choosing?

## Target
- File/function: [src/action/crossApp/wallet/sendTransaction.ts](src/action/crossApp/wallet/sendTransaction.ts) - crossApp sendTransaction: params [transaction], method privy_sendSmartWalletTx or eth_sendTransaction
- Entrypoint: privy.crossApp.wallet.sendTransaction({user, transaction, address, redirectUrl})
- Attacker controls: the transaction object (to, value, data, chainId) and the returned transactionHash
- Exploit idea: Return a connections entry with an attacker host and observe the outbound request.
- Invariant to test: Cross-app endpoints must be validated against a trusted registry before credentials are attached.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return a crafted provider_app_custom_api_url and assert crossApp sendTransaction: params [transaction] refuses to send the token.
