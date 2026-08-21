# Q2045: wallet_id accepted from the caller in raw-sign.ts

## Question
getWallet/updateWallet/rawSign take wallet_id from the caller; can an attacker pass a wallet id that is not theirs through rawSign(): same expiry-signed envelope for WalletRawSign and have the SDK build and sign an envelope for it?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Call the operation with a foreign wallet id.
- Invariant to test: Wallet ids must be validated against the authenticated user's linked accounts before signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign wallet id to rawSign(): same expiry-signed envelope for WalletRawSign and assert refusal before signing.
