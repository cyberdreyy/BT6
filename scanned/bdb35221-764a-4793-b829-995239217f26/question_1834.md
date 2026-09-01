# Q1834: Full access key added while tokens are locked - after vesting end

## Question
Can an unprivileged attacker satisfy `assert_eq!(self.get_locked_amount().0, 0)` at an instant when the schedule still locks tokens, then take a full access key over the account, after `end_timestamp`, when `get_unvested_amount` returns zero while release time remains, breaking the invariant that a full access key is only addable when the schedule locks nothing, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner.rs` - `LockupContract::transfer / add_full_access_key`
- Entrypoint: `transfer(amount, receiver_id)` and `add_full_access_key(new_public_key)` - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the amount, the receiver, the timing, and every state the getters read
- Exploit idea: Satisfy `assert_eq!(self.get_locked_amount().0, 0)` at an instant when the schedule still locks tokens, then take a full access key over the account, after `end_timestamp`, when `get_unvested_amount` returns zero while release time remains.
- Invariant to test: A full access key is only addable when the schedule locks nothing.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the assertion at every schedule boundary.
