# Q2047: wallet_id accepted from the caller in get-wallet.ts

## Question
getWallet/updateWallet/rawSign take wallet_id from the caller; can an attacker pass a wallet id that is not theirs through getWallet(): WalletGet by wallet_id and have the SDK build and sign an envelope for it?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Call the operation with a foreign wallet id.
- Invariant to test: Wallet ids must be validated against the authenticated user's linked accounts before signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign wallet id to getWallet(): WalletGet by wallet_id and assert refusal before signing.
