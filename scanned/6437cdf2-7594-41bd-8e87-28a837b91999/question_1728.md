# Q1728: wallet address resolves the provider app in signMessage.ts

## Question
getCrossAppAccountByWalletAddress picks the first cross_app account whose embedded_wallets or smart_wallets contains the address; can an attacker cause two accounts to contain the same address so crossApp signMessage: params [message routes the request to the wrong provider app?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Construct a user with duplicate addresses across cross_app accounts.
- Invariant to test: Address to provider resolution must be unique and verified.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build duplicate-address accounts and assert crossApp signMessage: params [message refuses to guess.
