# Q2708: params object forwarded verbatim in update-wallet.ts

## Question
The params branch of the signed body is passed through unvalidated; can an attacker include extra params fields through updateWallet(): signs {version:1 that the server honours but the client never showed the user?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Add unexpected keys to the params object.
- Invariant to test: Only a validated params schema may be signed.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: add unknown params keys in updateWallet(): signs {version:1 and assert they are stripped or rejected.
