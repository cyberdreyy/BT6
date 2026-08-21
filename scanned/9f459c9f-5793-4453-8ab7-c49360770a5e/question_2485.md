# Q2485: headers object shared across retries in raw-sign.ts

## Question
fetch-retry replays the same request object up to three times; can an attacker exploit the retry of a signed wallet operation in rawSign(): same expiry-signed envelope for WalletRawSign so one authorization signature yields several executions?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Return a retryable status for a signing request and count executions.
- Invariant to test: A signed wallet operation must execute at most once.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: return 503 then 200 for rawSign(): same expiry-signed envelope for WalletRawSign's route and assert one execution.
