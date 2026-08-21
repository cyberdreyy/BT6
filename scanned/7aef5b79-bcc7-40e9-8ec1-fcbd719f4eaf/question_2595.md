# Q2595: chain_type chosen by the caller in raw-sign.ts

## Question
The signed body includes a caller-supplied chain_type; can an attacker mismatch chain_type against the wallet through rawSign(): same expiry-signed envelope for WalletRawSign so a signature valid on one chain is produced for a wallet on another?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Submit an ethereum method for a solana wallet id.
- Invariant to test: Chain type must be derived from the wallet record, not the request.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: mismatch chain_type and wallet in rawSign(): same expiry-signed envelope for WalletRawSign and assert rejection.
