# Q1549: cpi_transfer_obligation_owner_to_destination: withdraw releases more value than the external position burned [candidate-destinations-from-another-bank] [recipient-binding]

## Question
Can an unprivileged attacker call `kamino_withdraw` with candidate destinations from another bank or integration family so `cpi_transfer_obligation_owner_to_destination` releases more value than the corresponding external position actually burned, violating `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and causing `Critical: direct theft of withdrawn assets`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: candidate destinations from another bank or integration family
- Exploit idea: Audit redeem/share conversions, rounding, and cached external balances around withdraw flows. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: Compare external burned shares/assets to internal released value under adversarial amounts and assert no excess release occurs. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
