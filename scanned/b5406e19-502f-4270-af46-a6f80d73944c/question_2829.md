# Q2829: returned transaction hash unverified in signTypedData.ts

## Question
The transactionHash returned by the provider is surfaced without checking that it corresponds to the submitted transaction; can an attacker return an unrelated hash through crossApp signTypedData: params [address so the app reports success for a transaction that never happened, or for a different one?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Return an arbitrary hash and observe the app's success path.
- Invariant to test: Returned identifiers must be verified against the submitted request.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return an unrelated hash from crossApp signTypedData: params [address and assert verification.
