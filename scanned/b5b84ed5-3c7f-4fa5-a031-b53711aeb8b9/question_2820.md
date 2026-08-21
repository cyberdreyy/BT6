# Q2820: expiry header name is a constant string in sign-wallet-request.ts

## Question
PRIVY_REQUEST_EXPIRY_HEADER_NAME is spread into the header object by computed key; can an attacker inject a header of the same name through the extraHeaders path in SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) so the transmitted expiry differs from the signed one?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Pass privy-request-expiry in extraHeaders and compare the signed and sent values.
- Invariant to test: The transmitted expiry must equal the signed expiry.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a conflicting expiry to SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert the request is rejected.
