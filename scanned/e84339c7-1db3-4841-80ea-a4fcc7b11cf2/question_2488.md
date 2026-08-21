# Q2488: headers object shared across retries in update-wallet.ts

## Question
fetch-retry replays the same request object up to three times; can an attacker exploit the retry of a signed wallet operation in updateWallet(): signs {version:1 so one authorization signature yields several executions?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Return a retryable status for a signing request and count executions.
- Invariant to test: A signed wallet operation must execute at most once.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: return 503 then 200 for updateWallet(): signs {version:1's route and assert one execution.
