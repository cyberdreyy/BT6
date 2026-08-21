# Q3805: get-wallet is unsigned in raw-sign.ts

## Question
getWallet issues a plain authenticated GET with no authorization signature; can an attacker use rawSign(): same expiry-signed envelope for WalletRawSign to enumerate wallet metadata (ids, signers) for wallets reachable with a session token alone?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Call the read path for wallet ids not owned by the session.
- Invariant to test: Wallet metadata reads must be scoped to the authenticated owner.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: read a foreign wallet id through rawSign(): same expiry-signed envelope for WalletRawSign and assert refusal.
