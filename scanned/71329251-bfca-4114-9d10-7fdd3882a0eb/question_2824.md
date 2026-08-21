# Q2824: returned transaction hash unverified in getProviderAccessTokenOrRelink.ts

## Question
The transactionHash returned by the provider is surfaced without checking that it corresponds to the submitted transaction; can an attacker return an unrelated hash through getProviderAccessTokenOrRelink: cached token from storage else relink so the app reports success for a transaction that never happened, or for a different one?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Return an arbitrary hash and observe the app's success path.
- Invariant to test: Returned identifiers must be verified against the submitted request.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return an unrelated hash from getProviderAccessTokenOrRelink: cached token from storage else relink and assert verification.
