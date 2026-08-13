# Q2312: cpi_transfer_withdraw_intermediary_ata_to_destination: withdraw releases more value than the external position burned [duplicate-metas-altering-intermediary-or] [round-trip]

## Question
Can an unprivileged attacker call `juplend_withdraw` with duplicate metas altering intermediary or destination interpretation so `cpi_transfer_withdraw_intermediary_ata_to_destination` releases more value than the corresponding external position actually burned, violating `intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable` and causing `Critical: direct theft of redeemed assets`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `cpi_transfer_withdraw_intermediary_ata_to_destination`
- Entrypoint: `juplend_withdraw`
- Attacker controls: duplicate metas altering intermediary or destination interpretation
- Exploit idea: Audit redeem/share conversions, rounding, and cached external balances around withdraw flows. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable
- Expected Immunefi impact: Critical: direct theft of redeemed assets
- Fast validation: Compare external burned shares/assets to internal released value under adversarial amounts and assert no excess release occurs. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
