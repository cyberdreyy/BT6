# Q1619: cpi_transfer_obligation_owner_to_destination: withdraw burns the right derivative but from the wrong owner context [same-slot-withdraw-plus-harvest] [recipient-binding]

## Question
Can an unprivileged attacker invoke `kamino_withdraw` with same-slot withdraw plus harvest or another transfer-using path so `cpi_transfer_obligation_owner_to_destination` burns the right derivative asset from the wrong owner context, violating `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and causing `Critical: direct theft of withdrawn assets`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: same-slot withdraw plus harvest or another transfer-using path
- Exploit idea: Check owner binding for external positions and obligation ownership during redeem paths. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: Mix owner/position pairs across users and assert the withdraw path rejects every mismatched owner context. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
