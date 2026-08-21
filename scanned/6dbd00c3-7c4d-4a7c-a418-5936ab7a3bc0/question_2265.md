# Q2265: no response signature verification in raw-sign.ts

## Question
The wallet-api response is consumed after only a method-name comparison; can an attacker return a response through rawSign(): same expiry-signed envelope for WalletRawSign whose signature field is arbitrary and have it used or broadcast?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Return an arbitrary signature and observe it flowing to the caller.
- Invariant to test: Responses carrying signatures must be verified against the request and the wallet key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a bogus signature from rawSign(): same expiry-signed envelope for WalletRawSign's route and assert verification fails.
