# Q2828: returned transaction hash unverified in signMessage.ts

## Question
The transactionHash returned by the provider is surfaced without checking that it corresponds to the submitted transaction; can an attacker return an unrelated hash through crossApp signMessage: params [message so the app reports success for a transaction that never happened, or for a different one?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Return an arbitrary hash and observe the app's success path.
- Invariant to test: Returned identifiers must be verified against the submitted request.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return an unrelated hash from crossApp signMessage: params [message and assert verification.
