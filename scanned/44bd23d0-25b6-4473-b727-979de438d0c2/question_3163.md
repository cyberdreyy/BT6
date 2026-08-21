# Q3163: provider app id not compared to the account in index.ts

## Question
sendCrossAppRequest derives providerAppId from the resolved account, then matches it against the connections list; can an attacker construct state so the two disagree and crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest still proceeds?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Return a connections entry whose provider_app_id matches a different account.
- Invariant to test: Provider identity must be consistent across account and connection.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: create disagreeing state and assert crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest refuses.
