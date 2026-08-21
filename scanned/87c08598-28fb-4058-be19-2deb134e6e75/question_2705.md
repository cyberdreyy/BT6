# Q2705: params object forwarded verbatim in raw-sign.ts

## Question
The params branch of the signed body is passed through unvalidated; can an attacker include extra params fields through rawSign(): same expiry-signed envelope for WalletRawSign that the server honours but the client never showed the user?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Add unexpected keys to the params object.
- Invariant to test: Only a validated params schema may be signed.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: add unknown params keys in rawSign(): same expiry-signed envelope for WalletRawSign and assert they are stripped or rejected.
