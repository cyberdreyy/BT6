# Q1624: cpi_transfer_obligation_owner_to_destination: withdraw burns the right derivative but from the wrong owner context [duplicate-metas-that-alter-which] [round-trip]

## Question
Can an unprivileged attacker invoke `kamino_withdraw` with duplicate metas that alter which destination is interpreted as canonical so `cpi_transfer_obligation_owner_to_destination` burns the right derivative asset from the wrong owner context, violating `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and causing `Critical: direct theft of withdrawn assets`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: duplicate metas that alter which destination is interpreted as canonical
- Exploit idea: Check owner binding for external positions and obligation ownership during redeem paths. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: Mix owner/position pairs across users and assert the withdraw path rejects every mismatched owner context. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
