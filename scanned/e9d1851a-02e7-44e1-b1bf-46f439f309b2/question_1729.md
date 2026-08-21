# Q1729: wallet address resolves the provider app in signTypedData.ts

## Question
getCrossAppAccountByWalletAddress picks the first cross_app account whose embedded_wallets or smart_wallets contains the address; can an attacker cause two accounts to contain the same address so crossApp signTypedData: params [address routes the request to the wrong provider app?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Construct a user with duplicate addresses across cross_app accounts.
- Invariant to test: Address to provider resolution must be unique and verified.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build duplicate-address accounts and assert crossApp signTypedData: params [address refuses to guess.
