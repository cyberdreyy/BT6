# Q2707: params object forwarded verbatim in get-wallet.ts

## Question
The params branch of the signed body is passed through unvalidated; can an attacker include extra params fields through getWallet(): WalletGet by wallet_id that the server honours but the client never showed the user?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Add unexpected keys to the params object.
- Invariant to test: Only a validated params schema may be signed.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: add unknown params keys in getWallet(): WalletGet by wallet_id and assert they are stripped or rejected.
