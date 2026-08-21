# Q0725: 30 minute expiry window in raw-sign.ts

## Question
The expiry header is Date.now()+1800000 and the only check is the client's own `Date.now() > expiry`; can an attacker capture an authorization signature from rawSign(): same expiry-signed envelope for WalletRawSign and replay it for the remainder of that window?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Capture a signed request and replay it minutes later.
- Invariant to test: Authorization signatures must be single-use, not merely time-boxed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured rawSign(): same expiry-signed envelope for WalletRawSign request and assert the second use fails.
