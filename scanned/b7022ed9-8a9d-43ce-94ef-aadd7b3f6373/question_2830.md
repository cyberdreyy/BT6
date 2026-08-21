# Q2830: returned transaction hash unverified in sendTransaction.ts

## Question
The transactionHash returned by the provider is surfaced without checking that it corresponds to the submitted transaction; can an attacker return an unrelated hash through crossApp sendTransaction: params [transaction] so the app reports success for a transaction that never happened, or for a different one?

## Target
- File/function: [src/action/crossApp/wallet/sendTransaction.ts](src/action/crossApp/wallet/sendTransaction.ts) - crossApp sendTransaction: params [transaction], method privy_sendSmartWalletTx or eth_sendTransaction
- Entrypoint: privy.crossApp.wallet.sendTransaction({user, transaction, address, redirectUrl})
- Attacker controls: the transaction object (to, value, data, chainId) and the returned transactionHash
- Exploit idea: Return an arbitrary hash and observe the app's success path.
- Invariant to test: Returned identifiers must be verified against the submitted request.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return an unrelated hash from crossApp sendTransaction: params [transaction] and assert verification.
