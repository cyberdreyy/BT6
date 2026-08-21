# Q3585: wallet-api errors surface raw responses in raw-sign.ts

## Question
Errors from these routes are wrapped with code and error text; can an attacker trigger an error through rawSign(): same expiry-signed envelope for WalletRawSign whose message discloses another user's wallet identifiers?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Force error responses and inspect the propagated text.
- Invariant to test: Error text must not disclose identifiers of resources the caller does not own.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: force a 403 in rawSign(): same expiry-signed envelope for WalletRawSign and assert the surfaced error carries no foreign identifiers.
