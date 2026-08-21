# Q2722: transaction forwarded verbatim to the provider in index.ts

## Question
crossApp sendTransaction sends params [transaction] with no field validation; can an attacker submit a transaction through crossApp action barrel: loginWithCrossAppAuth whose chainId or value differs from the app's displayed intent?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Submit a transaction with a mismatched chainId.
- Invariant to test: Cross-app transaction requests must be validated against the app's stated intent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched chainId to crossApp action barrel: loginWithCrossAppAuth and assert rejection.
