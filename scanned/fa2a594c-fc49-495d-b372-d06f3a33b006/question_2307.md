# Q2307: cpi_transfer_withdraw_intermediary_ata_to_destination: withdraw releases more value than the external position burned [same-slot-withdraw-plus-another] [recipient-binding]

## Question
Can an unprivileged attacker call `juplend_withdraw` with same-slot withdraw plus another transfer-using integration path so `cpi_transfer_withdraw_intermediary_ata_to_destination` releases more value than the corresponding external position actually burned, violating `intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable` and causing `Critical: direct theft of redeemed assets`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `cpi_transfer_withdraw_intermediary_ata_to_destination`
- Entrypoint: `juplend_withdraw`
- Attacker controls: same-slot withdraw plus another transfer-using integration path
- Exploit idea: Audit redeem/share conversions, rounding, and cached external balances around withdraw flows. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable
- Expected Immunefi impact: Critical: direct theft of redeemed assets
- Fast validation: Compare external burned shares/assets to internal released value under adversarial amounts and assert no excess release occurs. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
