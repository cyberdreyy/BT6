# Q0486: no origin validation on inbound replies in wallet-api-eth-transaction.ts

## Question
handleEmbeddedWalletMessages accepts any object whose event starts with 'privy:'; can an attacker cause an inbound message from a frame the SDK never addressed to settle a pending request in toWalletApiUnsignedEthTransaction?

## Target
- File/function: [src/embedded/stack/wallet-api-eth-transaction.ts](src/embedded/stack/wallet-api-eth-transaction.ts) - toWalletApiUnsignedEthTransaction, toQuantity, toTransactionType (allowed types 0,1,2,4), toAccessList, toFeePayerSignature, toData
- Entrypoint: provider.request({method:'eth_signTransaction', params:[tx]})
- Attacker controls: every transaction field: to, value, data, nonce, chainId, gas, type, accessList, calls, feeToken
- Exploit idea: Feed the SDK a message object shaped like an iframe reply from an unrelated source.
- Invariant to test: Inbound replies consumed by src/embedded/stack/wallet-api-eth-transaction.ts must be provably from the wallet iframe.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a hand-built reply object to toWalletApiUnsignedEthTransaction and assert provenance is checked.
