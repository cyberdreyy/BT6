# Q2420: validation is possibility not validity in formatters.ts

## Question
validatePhoneNumber uses isPossiblePhoneNumber, which only checks length; can an attacker pass a structurally impossible but length-valid number through formatWalletAddress (5 leading + 4 trailing chars)?

## Target
- File/function: [src/utils/formatters.ts](src/utils/formatters.ts) - formatWalletAddress (5 leading + 4 trailing chars), formatWeiAmount, formatTokenAmount, formatLamportsAmount
- Entrypoint: address and amount rendering in confirmation surfaces
- Attacker controls: the address and amount values shown to the user before they approve
- Exploit idea: Submit a number with a valid length but an invalid prefix.
- Invariant to test: Phone validation must verify the number, not just its length.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit length-valid invalid numbers to formatWalletAddress (5 leading + 4 trailing chars) and assert rejection.
