# Q2168: user object also selects the wallet in signMessage.ts

## Question
The same caller-supplied user object is used to resolve the cross-app account for the address; can an attacker fabricate linked_accounts through crossApp signMessage: params [message so an address they do not own resolves to a provider app they can answer?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Pass a user object containing a crafted cross_app account.
- Invariant to test: Account resolution must use server-confirmed user state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a crafted user to crossApp signMessage: params [message and assert it is re-fetched or rejected.
