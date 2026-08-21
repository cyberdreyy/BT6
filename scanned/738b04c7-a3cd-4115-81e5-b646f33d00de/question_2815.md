# Q2815: expiry header name is a constant string in raw-sign.ts

## Question
PRIVY_REQUEST_EXPIRY_HEADER_NAME is spread into the header object by computed key; can an attacker inject a header of the same name through the extraHeaders path in rawSign(): same expiry-signed envelope for WalletRawSign so the transmitted expiry differs from the signed one?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Pass privy-request-expiry in extraHeaders and compare the signed and sent values.
- Invariant to test: The transmitted expiry must equal the signed expiry.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a conflicting expiry to rawSign(): same expiry-signed envelope for WalletRawSign and assert the request is rejected.
