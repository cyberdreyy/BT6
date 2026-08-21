# Q1733: wallet address resolves the provider app in index.ts

## Question
getCrossAppAccountByWalletAddress picks the first cross_app account whose embedded_wallets or smart_wallets contains the address; can an attacker cause two accounts to contain the same address so crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest routes the request to the wrong provider app?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Construct a user with duplicate addresses across cross_app accounts.
- Invariant to test: Address to provider resolution must be unique and verified.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build duplicate-address accounts and assert crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest refuses to guess.
