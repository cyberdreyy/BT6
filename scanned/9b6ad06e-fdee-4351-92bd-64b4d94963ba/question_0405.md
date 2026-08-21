# Q0405: no request/response correlation id in getCrossAppAccountByWalletAddress.ts

## Question
The request carries only content and a timestamp; can an attacker deliver a response to getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address that belongs to a different cross-app request so the caller associates the wrong result?

## Target
- File/function: [src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts](src/action/crossApp/wallet/utils/getCrossAppAccountByWalletAddress.ts) - getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address
- Entrypoint: privy.crossApp.wallet.signMessage({address, ...})
- Attacker controls: the address argument and the set of cross_app accounts linked to the user
- Exploit idea: Issue two cross-app requests and cross the responses.
- Invariant to test: Cross-app responses must be correlated by an unguessable request id.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: cross two getCrossAppAccountByWalletAddress: first cross_app account whose embedded_wallets or smart_wallets contains the address responses and assert the mismatch is detected.
