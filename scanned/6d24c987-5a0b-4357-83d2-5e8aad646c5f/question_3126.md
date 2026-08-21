# Q3126: crypto resolver falls back to globals in wallet-api-eth-transaction.ts

## Question
resolveCrypto defaults digest and randomUUID to globalThis.crypto; can an attacker in the page substitute those globals so toWalletApiUnsignedEthTransaction produces predictable PKCE verifiers or idempotency keys?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Replace globalThis.crypto before constructing the client and observe generated values.
- Invariant to test: Security-critical randomness must not be taken from a mutable global at call time.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: stub globalThis.crypto with a deterministic implementation and assert toWalletApiUnsignedEthTransaction detects or refuses it.
