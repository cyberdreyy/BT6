# Q3676: add() skips the access token check in server mode in wallet-api-eth-transaction.ts

## Question
In user-controlled-server-wallets-only mode, add() creates through the wallet-api without the local access-token guard the other branch applies; can an attacker use toWalletApiUnsignedEthTransaction to add a wallet without a live session?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Set the config mode and call add with no token present.
- Invariant to test: Every wallet-creating branch must require an authenticated session.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: clear tokens, set server mode and assert toWalletApiUnsignedEthTransaction refuses.
