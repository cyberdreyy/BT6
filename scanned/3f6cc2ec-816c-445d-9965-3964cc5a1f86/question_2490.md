# Q2490: headers object shared across retries in sign-wallet-request.ts

## Question
fetch-retry replays the same request object up to three times; can an attacker exploit the retry of a signed wallet operation in SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) so one authorization signature yields several executions?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Return a retryable status for a signing request and count executions.
- Invariant to test: A signed wallet operation must execute at most once.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: return 503 then 200 for SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)'s route and assert one execution.
