# Q1647: cpi_transfer_obligation_owner_to_destination: withdraw path leaves internal debt/value view stale after CPI [boundary-sized-withdrawals-near-one] [recipient-binding]

## Question
Can an unprivileged attacker call `kamino_withdraw` with boundary-sized withdrawals near one-unit transfer differences so `cpi_transfer_obligation_owner_to_destination` completes the external CPI but leaves internal value view stale, breaking `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and causing `Critical: direct theft of withdrawn assets`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: boundary-sized withdrawals near one-unit transfer differences
- Exploit idea: Audit whether post-withdraw internal state, caches, and share accounting are refreshed from the exact redeemed value. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: After controlled withdraws, immediately try dependent borrow/withdraw paths and assert the internal value view matches the external post-state. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
