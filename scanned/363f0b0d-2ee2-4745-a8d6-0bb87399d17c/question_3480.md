# Q3480: app id read from AppApi at sign time in sign-wallet-request.ts

## Question
The envelope's privy-app-id is read from context.app.appId when signing; can an attacker change the app context between signing and sending in SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) so the header and the signature disagree?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Swap the app context mid-call.
- Invariant to test: App identity must be captured once and enforced consistently.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: swap the app context mid-call in SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert rejection.
