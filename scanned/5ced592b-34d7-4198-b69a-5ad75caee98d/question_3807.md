# Q3807: get-wallet is unsigned in get-wallet.ts

## Question
getWallet issues a plain authenticated GET with no authorization signature; can an attacker use getWallet(): WalletGet by wallet_id to enumerate wallet metadata (ids, signers) for wallets reachable with a session token alone?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Call the read path for wallet ids not owned by the session.
- Invariant to test: Wallet metadata reads must be scoped to the authenticated owner.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: read a foreign wallet id through getWallet(): WalletGet by wallet_id and assert refusal.
