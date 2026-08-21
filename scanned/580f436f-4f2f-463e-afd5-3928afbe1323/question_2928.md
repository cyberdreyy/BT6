# Q2928: signature not bound to the access token in update-wallet.ts

## Question
The envelope commits to app id and expiry but not to the session token used to authenticate; can an attacker present a signature from updateWallet(): signs {version:1 together with a different session token?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Pair a captured signature with another token.
- Invariant to test: Authorization signatures must be bound to the session that produced them.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: cross a captured signature with another session and assert rejection.
