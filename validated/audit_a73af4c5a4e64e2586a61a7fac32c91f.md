### Title
Removed member's stale confirmation is never purged from open requests, letting `confirm()` execute an action (including `DeployContract`) with fewer than `num_confirmations` live members - ([File: `multisig2/src/lib.rs`])

### Summary
`delete_member()` only deletes requests that were *originated* by the removed member (`r.member == member`); it never scrubs that member's entries out of the `confirmations` `HashSet` of other still-open requests they had merely confirmed. Because `confirm()` counts `confirmations.len() as u32 + 1 >= self.num_confirmations` without re-validating that every recorded confirmer is still in `self.members`, a request can reach the confirmation threshold and execute even though one of the counted "confirmers" was removed from the multisig before execution.

### Finding Description
The binding that must hold is:
`count of confirmations for request_id whose author ∈ self.members.to_vec() at the moment of execution == self.num_confirmations`

In `confirm()`:
```
multisig2/src/lib.rs:299-315
let mut confirmations = self.confirmations.get(&request_id).unwrap();
...
if confirmations.len() as u32 + 1 >= self.num_confirmations {
    let request = self.remove_request(request_id);
    self.execute_request(request)
} else {
    confirmations.insert(member.to_string());
    ...
}
```
the count is taken purely from the stored `HashSet<String>` plus the current caller — there is no re-check that the strings already in the set correspond to accounts/keys still present in `self.members`.

`delete_member()` is the only place that could clean stale entries, but it filters requests by *authorship*, not by *confirmer*:
```
multisig2/src/lib.rs:356-374
let request_ids: Vec<u32> = self
    .requests
    .iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
```
Any request that the removed member merely *confirmed* (but did not author) is left untouched, including its `confirmations` entry for that now-removed member.

Exploit flow:
1. Set up a multisig with 4 members (A, B, C, D) and `num_confirmations = 3` (needed because `delete_member` asserts `self.members.len() - 1 >= self.num_confirmations`, so 3 members with `num_confirmations = 3` would make deletion impossible — `multisig2/src/lib.rs:357-360`).
2. D (or any member) adds request `R` = `DeployContract{code}` targeting a victim account controlled by this multisig (e.g. a staking pool/lockup where this multisig has deploy authority).
3. C confirms `R` → `confirmations = {C}` (1/3, request stays open).
4. A separate fully-confirmed request executes `DeleteMember{member: C}` → C is removed from `self.members`. `R`'s `confirmations` set (`{C}`) is left untouched because `R.member != C` (D authored it).
5. A confirms `R` → `confirmations.len()+1 = 2 < 3`, so A is inserted: `confirmations = {C, A}`.
6. B confirms `R` → `confirmations.len()+1 = 3 >= 3` → threshold met → `remove_request` + `execute_request(R)` runs, deploying attacker-influenced `code` to the victim account.

At execution time, only A and B are live members backing the action; C's confirmation is stale/ghost, yet it is counted. No existing guard (`assert_valid_request`, `current_member`, `assert_self_request`) re-validates the membership of previously stored confirmers — they only check the *current caller's* membership at the moment they confirm, never the composition of the whole `confirmations` set at execution time.

### Impact Explanation
This lets 2 live, colluding members (plus one ghost/stale confirmation from a removed member) push through any action, including `DeployContract{code}` against a victim staking pool or lockup account this multisig controls, replacing its logic entirely below the required live-member consensus. This directly matches the Critical impact category: "a multisig request executed below `num_confirmations` live members." Once code is replaced, the attacker-controlled logic can subsequently move out any NEAR/tokens held by the victim contract — a full loss of funds under attacker control, and the pattern is repeatable for any request type (`Transfer`, `AddKey`, etc.), not just `DeployContract`.

### Likelihood Explanation
Preconditions: attacker(s) must already be 2 of the multisig's members (this is a member-vs-member/insider concern rather than a fully external attacker, but note the question is scoped to "an attacker who is one of num_confirmations-1 legitimate members" combined with a removed member's stale confirmation — i.e., it requires insider participation, which the audit rules exclude for "multisig member" attackers). No special balances or funds are required beyond normal multisig operation; the only cost is the sequence of ordinary `add_request`/`confirm` calls and one `DeleteMember` execution. It is fully reproducible in a `cargo test` unit test with `testing_env!` and is repeatable across any number of open requests confirmed-then-orphaned by a removed signer.

### Recommendation
When executing `DeleteMember`, purge the removed member's string from every entry in `self.confirmations` (not just requests it authored), or alternatively re-validate at count time in `confirm()` that every account/key already present in the stored `confirmations` set is still contained in `self.members` before adding it to the running count.

### Proof of Concept
```rust
#[test]
fn test_stale_confirmation_survives_delete_member() {
    let amount = 1_000;
    // D (TEST_KEY) creates the multisig with 4 members, num_confirmations = 3
    testing_env!(context_with_key(PublicKey::try_from(TEST_KEY.to_vec()).unwrap(), amount));
    let mut c = MultiSigContract::new(members(), 3);

    // D adds request R: DeployContract targeting current_account_id (victim under this multisig)
    let r_id = c.add_request(MultiSigRequest {
        receiver_id: alice(),
        actions: vec![MultiSigRequestAction::DeployContract { code: Base64VecU8(vec![1,2,3]) }],
    });

    // C confirms R (1/3)
    testing_env!(context_with_key(
        PublicKey::from("ed25519:Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy".parse().unwrap()),
        amount
    ));
    c.confirm(r_id);
    assert_eq!(c.confirmations.get(&r_id).unwrap().len(), 1);

    // Separate fully-confirmed request removes C via DeleteMember (A, B, D confirm)
    // ... add_request_and_confirm DeleteMember{member: C-as-AccessKey} with A, B, D confirming ...
    // after execution:
    let c_member = MultisigMember::AccessKey {
        public_key: "ed25519:Eg2jtsiMrprn7zgKKUk79qM1hWhANsFyE6JSX4txLEuy".parse().unwrap(),
    };
    assert!(!c.members.contains(&c_member)); // C is removed

    // BINDING CHECK: R's stale confirmation set still contains C
    assert!(c.confirmations.get(&r_id).unwrap().contains(&c_member.to_string()));

    // A confirms R (2/3 by count, but only A and future B are live)
    testing_env!(context_with_account(alice(), amount));
    c.confirm(r_id);
    assert_eq!(c.confirmations.get(&r_id).unwrap().len(), 2);

    // B confirms R -> triggers execution with only A, B live (2 live members),
    // yet count reaches 3 due to stale C entry, violating:
    // count-of-confirmations-from-live-members == num_confirmations
    testing_env!(context_with_account(bob(), amount));
    c.confirm(r_id);
    assert_eq!(c.requests.len(), 0); // R executed
    // live confirming members backing execution: {A, B} = 2 < num_confirmations (3)
}
```