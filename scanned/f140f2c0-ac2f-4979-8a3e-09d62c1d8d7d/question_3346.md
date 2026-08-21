# Q3346: base64 and utf8 conversions lose bytes in wallet-api-eth-transaction.ts

## Question
The encoding helpers convert signing payloads through utf8 and base64; can an attacker submit bytes that are not valid UTF-8 so toWalletApiUnsignedEthTransaction signs a lossy re-encoding of the intended payload?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Pass a payload with lone surrogates or 0xFF bytes and compare round-tripped output.
- Invariant to test: Encoding round trips must be byte-exact for anything that gets signed.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: round-trip non-UTF-8 byte sequences through toWalletApiUnsignedEthTransaction and assert byte equality.
