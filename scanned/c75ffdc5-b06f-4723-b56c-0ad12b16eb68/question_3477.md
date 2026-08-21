# Q3477: app id read from AppApi at sign time in get-wallet.ts

## Question
The envelope's privy-app-id is read from context.app.appId when signing; can an attacker change the app context between signing and sending in getWallet(): WalletGet by wallet_id so the header and the signature disagree?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Swap the app context mid-call.
- Invariant to test: App identity must be captured once and enforced consistently.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: swap the app context mid-call in getWallet(): WalletGet by wallet_id and assert rejection.
