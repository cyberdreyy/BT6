# Q3016: solana rpc path only implements signMessage in wallet-api-eth-transaction.ts

## Question
walletRpc's solana branch handles signMessage and returns undefined for anything else; can an attacker exploit the undefined return in toWalletApiUnsignedEthTransaction so a caller treats a failed operation as success?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Call an unsupported solana method and inspect the resolved value.
- Invariant to test: Unsupported operations must reject rather than resolve undefined.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call an unsupported method through toWalletApiUnsignedEthTransaction and assert it rejects.
