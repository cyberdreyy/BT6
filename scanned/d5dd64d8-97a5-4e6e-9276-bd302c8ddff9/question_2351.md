# Q2351: cpi_transfer_withdraw_intermediary_ata_to_destination: withdraw refresh and redeem operate on different external state [boundary-sized-withdrawals-near-one] [recipient-binding]

## Question
Can an unprivileged attacker invoke `juplend_withdraw` with boundary-sized withdrawals near one-unit residual transfers so `cpi_transfer_withdraw_intermediary_ata_to_destination` refreshes one external state object but redeems another, violating `intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable` and leading to `Critical: direct theft of redeemed assets`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `cpi_transfer_withdraw_intermediary_ata_to_destination`
- Entrypoint: `juplend_withdraw`
- Attacker controls: boundary-sized withdrawals near one-unit residual transfers
- Exploit idea: Where refresh precedes withdraw, ensure the refreshed reserve/obligation/position is exactly the one later burned or redeemed. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable
- Expected Immunefi impact: Critical: direct theft of redeemed assets
- Fast validation: Feed mismatched external contexts and assert withdraw rejects unless refresh and redeem are bound to the same object. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
