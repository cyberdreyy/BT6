# Q2818: expiry header name is a constant string in update-wallet.ts

## Question
PRIVY_REQUEST_EXPIRY_HEADER_NAME is spread into the header object by computed key; can an attacker inject a header of the same name through the extraHeaders path in updateWallet(): signs {version:1 so the transmitted expiry differs from the signed one?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Pass privy-request-expiry in extraHeaders and compare the signed and sent values.
- Invariant to test: The transmitted expiry must equal the signed expiry.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a conflicting expiry to updateWallet(): signs {version:1 and assert the request is rejected.
