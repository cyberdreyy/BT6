# Q3808: get-wallet is unsigned in update-wallet.ts

## Question
getWallet issues a plain authenticated GET with no authorization signature; can an attacker use updateWallet(): signs {version:1 to enumerate wallet metadata (ids, signers) for wallets reachable with a session token alone?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Call the read path for wallet ids not owned by the session.
- Invariant to test: Wallet metadata reads must be scoped to the authenticated owner.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: read a foreign wallet id through updateWallet(): signs {version:1 and assert refusal.
