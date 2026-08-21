# Q3478: app id read from AppApi at sign time in update-wallet.ts

## Question
The envelope's privy-app-id is read from context.app.appId when signing; can an attacker change the app context between signing and sending in updateWallet(): signs {version:1 so the header and the signature disagree?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Swap the app context mid-call.
- Invariant to test: App identity must be captured once and enforced consistently.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: swap the app context mid-call in updateWallet(): signs {version:1 and assert rejection.
