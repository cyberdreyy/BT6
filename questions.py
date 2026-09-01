import json
import os

from decouple import config

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 20
# todo: the GitLab namespace/project path, for example group/project
SOURCE_REPO = 'near/core-contracts'
# todo: the name of the repository
REPO_NAME = 'core-contracts'

run_number = os.environ.get('GITHUB_RUN_NUMBER', '0')


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index"""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "repositories.json")
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"

scope_files = [
    # =================================================================================
    # LENS: FROM A TRANSACTION ANY ACCOUNT CAN SEND TO SOMEBODY ELSE'S NEAR LEAVING A
    # CORE CONTRACT ACCOUNT.
    # These are NEAR's reference custody contracts: a staking pool holding delegator
    # NEAR, a lockup/vesting account holding a grantee's NEAR, the factories that
    # create and whitelist both, the multisig that guards accounts, the voting poll
    # that unlocks transfers, and wNEAR which must stay 1:1 with the NEAR it holds.
    # Untrusted input enters through doors an unprivileged party fully controls:
    # `deposit` / `deposit_and_stake` / `stake` / `unstake` / `withdraw` / `ping` on any
    # pool, a bare NEAR transfer to a contract account, `create` / `create_staking_pool`
    # on the open payable factories with attacker-chosen args, `near_deposit` /
    # `near_withdraw` / `ft_transfer_call` / `storage_unregister` on wNEAR, a contract
    # the attacker deploys and names (fake pool, fake whitelist, fake poll) whose return
    # value comes back into an `assert_self()` callback, and the unguarded `#[no_mangle]`
    # exports of `state-manipulation`. It all ends in one place: the NEAR balance of a
    # contract account and the per-account claims recorded against it. A file belongs
    # here only if a solvency, authorisation, schedule or settlement invariant must hold
    # across it.
    # =================================================================================

    # -- Staking pool: delegator NEAR, share price and epoch reward accounting ------------
    "staking-pool/src/lib.rs",
    "staking-pool/src/internal.rs",

    # -- Lockup / vesting: what an account may release and when ---------------------------
    "lockup/src/lib.rs",
    "lockup/src/internal.rs",
    "lockup/src/owner.rs",
    "lockup/src/owner_callbacks.rs",
    "lockup/src/foundation.rs",
    "lockup/src/foundation_callbacks.rs",
    "lockup/src/getters.rs",
    "lockup/src/types.rs",
    "lockup/src/gas.rs",

    # -- Factories: anyone can call them, and what they create becomes trusted -------------
    "lockup-factory/src/lib.rs",
    "lockup-factory/src/types.rs",
    "lockup-factory/src/utils.rs",
    "staking-pool-factory/src/lib.rs",
    "staking-pool-factory/src/utils.rs",
    "multisig-factory/src/lib.rs",

    # -- Whitelist and voting: the two external facts a lockup acts on ---------------------
    "whitelist/src/lib.rs",
    "voting/src/lib.rs",

    # -- Multisig: k-of-n authorisation over accounts that hold funds ----------------------
    "multisig/src/lib.rs",
    "multisig2/src/lib.rs",

    # -- wNEAR: a token that must stay backed 1:1 by the NEAR the contract holds -----------
    "w-near/src/lib.rs",
    "w-near/src/w_near.rs",
    "w-near/src/legacy_storage.rs",

    # -- Raw state writer deployed onto live accounts --------------------------------------
    "state-manipulation/src/lib.rs",

    # =================================================================================
    # NOT IN THIS VARIANT:
    # * `**/tests/**`, `**/tests.rs`, `**/test_utils.rs`, `**/src/tests/**` and the
    #   `#[cfg(test)] mod tests` blocks inside the files above - tests, fixtures, mocks.
    # * `**/res/*.wasm`, `**/build.sh`, `scripts/**`, `.buildkite/**` - build artefacts
    #   and tooling.
    # * `*.toml`, `Cargo.lock`, `*.md`, `LICENSE-*`, `CODEOWNERS`, `rustfmt.toml` -
    #   configuration and documentation.
    # =================================================================================
]


target_scopes = [
    "Critical. A STAKE SHARE THAT COSTS LESS THAN IT REDEEMS. `internal_stake` mints `num_shares_from_staked_amount_rounded_down(amount)` and charges `staked_amount_from_num_shares_rounded_down(num_shares)`, while `inner_unstake` burns `num_shares_from_staked_amount_rounded_up(amount)` and pays `staked_amount_from_num_shares_rounded_up(num_shares)`; the gap is meant to come out of the `STAKE_SHARE_PRICE_GUARANTEE_FUND` (1e12 yocto) seeded once in `StakingContract::new`, and every conversion is a U256 mul/div over `total_staked_balance` / `total_stake_shares`. Show an ordinary delegator - anyone who can attach a deposit - who calls `deposit_and_stake`, `stake`, `unstake`, `unstake_all`, `withdraw` in a shaped sequence (dust amounts, a price moved by `internal_ping`, `internal_save_account` deleting and recreating their row) and comes out ahead, or who leaves other delegators' shares redeeming for less than they were charged. Binding: for a round trip `deposit_and_stake(x)` -> `unstake_all` -> `withdraw_all`, NEAR received <= x, and the sum over all accounts of `unstaked + staked_amount_from_num_shares_rounded_down(stake_shares)` <= `env::account_balance() + env::account_locked_balance()`.",

    "Critical. REWARDS THAT WERE NEVER EARNED, OR AN EPOCH THAT CAN NEVER BE PINGED AGAIN. `internal_ping` computes `total_balance = env::account_locked_balance() + env::account_balance() - env::attached_deposit()`, asserts `total_balance >= self.last_total_balance`, credits `total_reward - owners_fee` into `total_staked_balance` and mints the owner `num_shares_from_staked_amount_rounded_down(owners_fee)` at the NEW price, and `last_total_balance` is otherwise maintained by hand in `internal_deposit`, `internal_withdraw` and `on_stake_action`. Any account can transfer NEAR straight to the pool account, or call `ping` / `deposit` / `withdraw` at a chosen moment inside an epoch. Show an unprivileged delegator who has reward attributed to shares they bought after it accrued, who converts a bare transfer into shares, or who forces `total_balance < last_total_balance` - a `Promise::transfer` that failed, a rolled-back stake action, a drop in `locked` - so the `assert!` in `internal_ping` panics and every `ping`-guarded method (`deposit`, `stake`, `unstake`, `withdraw`) is permanently unusable for all delegators. Binding: after every call `last_total_balance` == `env::account_balance() + env::account_locked_balance()`, and `total_staked_balance` increases only by NEAR the epoch actually paid this pool.",

    "Critical. THE BALANCE IS DEBITED, THE NEAR NEVER ARRIVES. `internal_withdraw` asserts `unstaked_available_epoch_height <= env::epoch_height()`, decrements `account.unstaked` and `last_total_balance`, then fires `Promise::new(account_id).transfer(amount)` with NO callback; the only resolver in the contract is `on_stake_action`, which just re-`stake`s. `internal_save_account` deletes the account entry when both balances reach zero, and `inner_unstake` sets `unstaked_available_epoch_height = env::epoch_height() + NUM_EPOCHS_TO_UNLOCK`. Show an unprivileged delegator for whom the debited amount and the delivered amount differ: a transfer to a deleted or non-existent receiver that silently fails while the accounting says it was paid, `withdraw` and `withdraw_all` both settling the same unstaked NEAR, or an unlock gate satisfied earlier than `NUM_EPOCHS_TO_UNLOCK` epochs. Binding: NEAR that leaves the pool account for an account == the `unstaked` amount that account was debited, exactly once.",

    "Critical. A LOCKUP AT THE VICTIM'S ADDRESS, WITH THE ATTACKER'S PARAMETERS. `LockupFactory::create` is `#[payable]` and open to ANY caller: the caller chooses `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration` and - critically - `whitelist_account_id`, while the deployed address is fully determined by `hex::encode(&env::sha256(owner_account_id.as_bytes())[..20])` prefixed onto the factory account. Nothing ties the caller to the owner, and `on_lockup_create` (`assert_self()`, `is_promise_success()`) refunds `attached_deposit` to `predecessor_account_id` when creation fails. Show an unprivileged party who squats the deterministic address of a grant that has not been created yet, who deploys a lockup whose `staking_pool_whitelist_account_id` is a contract they control (so `select_staking_pool`'s `on_whitelist_is_whitelisted` approves a pool that keeps the deposit), or who gets the failure refund while the created account keeps the transferred NEAR. Binding: the `LockupArgs` living at `sha256(owner)[..20].<factory>` == the args the grant's real creator sent, and refunded deposit + NEAR left in the lockup == `env::attached_deposit()`, once.",

    "Critical. WHITELISTED WITHOUT BEING THE AUDITED POOL. `StakingPoolFactory::create_staking_pool` is open to anyone attaching `MIN_ATTACHED_BALANCE`: it checks only `staking_pool_id.find('.').is_none()` and `env::is_valid_account_id`, inserts into `staking_pool_account_ids`, then creates the account, transfers the deposit, deploys `staking_pool.wasm` and calls `new` with attacker-chosen `owner_id`, `stake_public_key` and `reward_fee_fraction`; `on_staking_pool_create` (`assert_self()`) calls `ext_whitelist::add_staking_pool` on success and refunds otherwise. `WhitelistContract::add_staking_pool` accepts it purely on `is_factory_whitelisted(env::predecessor_account_id())`, never inspects the code at that account, and only `assert_called_by_foundation` can `remove_staking_pool`. Show an unprivileged party who gets an account marked `is_whitelisted` that is not running the factory's pool code under the factory's args - a `new` that failed while the account survived, an account whose code or keys change after whitelisting, or an id that collides with an existing entry - so lockup contracts, whose ONLY check is `on_whitelist_is_whitelisted`, hand it vested NEAR. Binding: every account for which `is_whitelisted` returns true == an account running the factory-deployed `staking_pool.wasm` initialised with the args the factory passed.",

    "Critical. TOKENS THAT LEAVE BEFORE THEY UNLOCK. `get_locked_amount` takes `max(unreleased_amount.saturating_sub(termination_withdrawn_tokens), unvested_amount)` where `unreleased_amount` is a U256 `lockup_amount * time_left / release_duration` past `max(transfers_timestamp.saturating_add(lockup_duration), lockup_timestamp)`, `get_unvested_amount` is a second U256 ratio over the `VestingSchedule`, and `VestingInformation::VestingHash` / `None` collapse to `U128(0)`. `get_owners_balance` = `(env::account_balance() + get_known_deposited_balance()).saturating_sub(locked)`, `get_liquid_owners_balance` mins it with `get_account_balance()` (which subtracts `MIN_BALANCE_FOR_STORAGE`), and `owner::transfer` / `add_full_access_key` gate only on `assert_transfers_enabled` plus those numbers. Anyone can be an owner - `LockupFactory::create` hands out lockups to whoever asks. Show an owner who moves out more than the schedule permits: a boundary in `lockup_timestamp` vs `transfers_timestamp + lockup_duration`, a `release_duration` of 0 or 1 ns, a private `VestingHash` treated as nothing unvested, a `termination_withdrawn_tokens` subtraction that credits twice, or an `add_full_access_key` that makes the getters irrelevant. Binding: NEAR that can leave the lockup account at time T == `get_balance() - get_locked_amount()` under the schedule the contract was initialised with.",

    "Critical. THE LOCKUP BELIEVES A NUMBER NOBODY VERIFIED. `staking_information.deposit_amount` is the lockup's private bookkeeping of NEAR held somewhere else, and it flows straight into `get_known_deposited_balance` -> `get_owners_balance` -> what `transfer` will release. It is written only inside the `assert_self()` callbacks of `owner_callbacks.rs` and `foundation_callbacks.rs` (`on_staking_pool_deposit`, `on_staking_pool_deposit_and_stake`, `on_staking_pool_withdraw`, `on_get_account_total_balance`, `on_staking_pool_unstake_all`, `on_get_account_staked_balance_to_unstake`), around a `TransactionStatus` flag set by `set_staking_pool_status` and asserted by `assert_staking_pool_is_idle`; the values come from whatever contract `select_staking_pool` accepted. In parallel `check_transfers_vote` -> `on_get_result_from_transfer_poll` flips `TransfersInformation::TransfersEnabled` from a `VotingContract::get_result` reading, where `vote` and `ping` recompute `total_voted_stake` from `env::validator_stake` and finalise at 2/3. Show a party who is neither the owner nor the foundation making one of these facts wrong - a callback path where `is_promise_success()` is false yet state still moves, a `refresh_staking_pool_balance` against a pool reporting an inflated total, a `Busy` status that no call can ever clear, or a poll result read as enabled when 2/3 was never reached. Binding: `get_known_deposited_balance()` == the NEAR the selected pool actually owes this account, and `are_transfers_enabled()` == the poll's real 2/3 outcome.",

    "Critical. A REQUEST EXECUTED WITH FEWER LIVE CONFIRMATIONS THAN `num_confirmations`. In `multisig2` (and its `multisig` ancestor), `confirm` runs `assert_valid_request`, resolves `current_member()` - `MultisigMember::AccessKey { public_key: env::signer_account_pk() }` when `predecessor == current_account_id`, otherwise `MultisigMember::Account { account_id: predecessor }` - rejects a duplicate by `member.to_string()` inside a `HashSet<String>`, and executes the moment `confirmations.len() as u32 + 1 >= self.num_confirmations`. `delete_member` removes that member's own requests and their `num_requests_pk` but NOT the confirmations they already cast on other requests; `SetNumConfirmations` and `SetActiveRequestsLimit` mutate the threshold behind `assert_one_action_only`; `add_member` grants a function-call key restricted to `MULTISIG_METHOD_NAMES`; `MultisigFactory::create` lets anyone deploy such a contract with chosen `members`. Show a party who is not a current member getting a `MultiSigRequest` executed against funds: a stale confirmation from a removed (or removed-and-readded) member counted toward the threshold, a `to_string()` collision between an `Account` member and an `AccessKey` member, an action list where `assert_self_request` is satisfied by one action while a `Transfer` / `AddKey { permission: None }` in the same batch targets another receiver, or a threshold lowered inside the batch that authorises it. Binding: for every executed request, the count of distinct members currently in `self.members` that confirmed it == `num_confirmations`.",

    "Critical. wNEAR THAT IS NOT BACKED BY NEAR. `near_deposit` mints `env::attached_deposit()` minus, for an unregistered account, `storage_balance_bounds().min` which it keeps; `near_withdraw` does `assert_one_yocto()`, `ft.internal_withdraw(&account_id, amount)` and then `Promise::new(account_id).transfer(amount + 1)` with NO resolver; `impl_fungible_token_core!` and `impl_fungible_token_storage!` expose `ft_transfer`, `ft_transfer_call`, `ft_resolve_transfer`, `storage_deposit`, `storage_withdraw` and `storage_unregister { force }` over the same `FungibleToken`, while `legacy_storage::storage_minimum_balance` still reports the old bound. Show an unprivileged holder who ends with `ft_balance_of` credit the contract's NEAR cannot honour, or who takes out more NEAR than they burned: a registration charge counted twice or skipped, a failed `near_withdraw` transfer whose supply stays burned, a forced `storage_unregister` that destroys balance without releasing the NEAR behind it, or an `ft_resolve_transfer` refund that re-credits more than was taken. Binding: `ft.total_supply` == NEAR held by the contract minus the registered storage deposits, before and after every call.",

    "Critical. THE MISSING BINDING - what nobody built. Nothing in this repository re-derives solvency after a call: `StakingContract` never asserts `last_total_balance == sum(account.unstaked) + total_staked_balance`, nor compares either to the account's real NEAR; `LockupContract` never re-checks `deposit_amount` against the pool that holds it; `WhitelistContract` never looks at the code deployed at an account it whitelists; every value-moving `Promise::transfer` in `staking-pool`, `w-near`, `multisig` and both factories is fire-and-forget with no resolver; and `state-manipulation`'s `replace` / `clean` are `#[no_mangle]` exports that read `input()` and call `storage_write` / `storage_remove` on caller-supplied base64 keys with NO predecessor, owner or key check whatsoever - on any account where that wasm is still deployed, any caller rewrites contract state directly. Identify the FIRST point at which a value an unprivileged party chose - an attached deposit, a factory argument, a bare NEAR transfer into a contract account, a promise result from a contract they deployed, or a raw storage key - becomes a credited balance, a released transfer or an authorisation with nothing independently re-deriving it. Prove it with one `cargo test` asserting both the value used and the value that should have authorised it, and show that once they diverge nothing in these contracts reconciles them.",
]


scope_scan = [
]


def question_generator(target_file: str) -> str:
    """
    Generate custody and authorization audit questions for one core-contracts target.

    ```
    target_file format:
    "'File Name: staking-pool/src/internal.rs -> Scope: Critical. ...'"
    """

    prompt = f"""
    ```

    Generate custody and authorization security audit questions for this exact
    NEAR core-contracts target:

    {target_file}

    Project focus:
    `near/core-contracts` are NEAR's reference custody contracts: a staking pool holding
    delegator NEAR, a lockup/vesting account holding a grantee's NEAR, the open payable
    factories that create and whitelist both, the multisig guarding accounts, the voting
    poll that unlocks transfers, and wNEAR which must stay backed 1:1. Untrusted input
    enters through doors any unprivileged party controls: `deposit` / `deposit_and_stake` /
    `stake` / `unstake` / `withdraw` / `ping` on any pool, a bare NEAR transfer to a
    contract account, `LockupFactory::create` and `StakingPoolFactory::create_staking_pool`
    with attacker-chosen arguments, `MultisigFactory::create`, wNEAR's `near_deposit` /
    `near_withdraw` / `ft_transfer_call` / `storage_unregister`, a contract the attacker
    deploys and names (fake pool, fake whitelist, fake poll) whose return value comes back
    into an `assert_self()` callback, and the unguarded `#[no_mangle]` exports of
    `state-manipulation`. It all ends in one place: the NEAR balance of a contract account
    and the per-account claims recorded against it. Anything that moves NEAR a party is not
    entitled to, leaves claims exceeding assets, releases locked tokens early, or freezes
    funds permanently is the bug.

    Rules:
    * Treat `File Name:` as the exact file.
    * Treat `Scope:` as the ONLY impact to target.
    * Assume full repo context is accessible.
    * Do not ask for code or say anything is missing.
    * Use exact Rust symbols (module, struct, enum, fn, const, field) as they appear in the file.
    * EVERY question must close on a binding that must hold across a call, stated explicitly as
      an equality between two named values. Narrative questions are rejected.
    * Attacker is unprivileged only: anyone who can send a NEAR transaction, attach a deposit,
      delegate to and withdraw from any staking pool, call `ping` and any other open method,
      transfer NEAR directly to a contract account, create their own lockup / staking pool /
      multisig through the public factories with arguments they choose, hold and move wNEAR,
      and deploy contracts they control and name as a whitelist, pool, poll or transfer target.
    * Attacker is NOT the NEAR Foundation or the whitelist `foundation_account_id`, not the
      owner of a victim's lockup or pool, not a multisig member, not a full-access key holder
      on a victim account, and not a validator or node operator. They hold no victim key. No
      malicious validator, node or peer, no key compromise, no RPC or TLS interception, no
      local or physical access, no compromised dependency, no social engineering.
    * PROGRAM EXCLUSIONS - a question landing in any of these wastes the whole batch:
      - Tests, fixtures and mocks (`**/tests/**`, `**/tests.rs`, `**/test_utils.rs`,
        `**/src/tests/**`, `#[cfg(test)]` blocks), build artefacts and tooling
        (`**/res/*.wasm`, `**/build.sh`, `scripts/**`, `.buildkite/**`), `*.toml`,
        `Cargo.lock`, `*.md` are OUT OF SCOPE.
      - Gas or storage consumption, unbounded collections, denial of service, rate limiting,
        queue depth, resource exhaustion, memory hygiene and log volume are OUT OF SCOPE.
      - Griefing with no attacker gain, anything that only costs the attacker their own funds,
        and anything requiring the foundation, a contract owner, a multisig member or a
        redeploy are OUT OF SCOPE.
      - Defects in nearcore, `near-sdk`, `near-contract-standards`, `serde`, `borsh` or `uint`
        with no exploit path through this repository's own code are OUT OF SCOPE.
      - Also excluded: leaked keys, best-practice notes, feature requests, and theoretical
        findings with no demonstration.
      - A weakness in this repository that drives a dependency into unsafe behaviour stays in
        scope.
    * IN-SCOPE IMPACTS - every question must land on one and name it:
      Critical: NEAR or wNEAR moved out of a pool, lockup, multisig or the wNEAR contract by a
      party not entitled to it; claims (delegator balances, `ft.total_supply`, a lockup's
      owner balance) exceeding the NEAR actually held; locked or unvested tokens released
      before the schedule allows; an account whitelisted or a lockup deployed with parameters
      its rightful creator never chose; a multisig request executed with fewer live
      confirmations than `num_confirmations`; user funds permanently frozen by a panic or a
      status no call can clear.
      High: rewards or owner fees attributed to the wrong party; funds frozen for at least one
      epoch but recoverable; an accounting value (`deposit_amount`, `last_total_balance`,
      `get_owners_balance`) diverging from reality where another party settles on it.
    * Every question must be a concrete real-world scenario an unprivileged attacker can run
      against a deployed contract - a call they make with named arguments, a deposit they
      attach, a contract they deploy and name, a transfer they send. No speculative
      resource-hygiene or memory questions.
    * A panic or error is a finding only when it freezes funds or lets an unauthorised move
      through - say which.
    * Generate 40 to 80 high-signal questions.
    * At least 70% must land on a Critical impact rather than a High one.
    * Every question must be testable in this workspace by `cargo test` - a `testing_env!`
      unit test, a `near-sdk-sim` / `RuntimeStandalone` harness, or `near-workspaces` - with
      no mainnet.
    * Avoid generic checklist questions and repeated root causes.
    * Prefer questions that name TWO values that must be equal and ask whether they are: the
      NEAR debited and the NEAR delivered, the sum of account claims and the contract balance,
      the shares charged and the shares redeemed, the confirmations counted and the live
      members who gave them, the schedule's locked amount and what can leave, the args a
      factory deployed and the args its caller was entitled to choose.

    Known dead ends - do NOT generate questions about these:
    * Anything needing the foundation account, a contract owner, a multisig member, a victim
      key or a redeploy.
    * A bug in a dependency with no reachable path through this repository.
    * Gas, storage growth, log size, or an attacker burning only their own funds with nobody
      else harmed.
    * Findings only reproducible in tests, fixtures or build scripts.

    Core bindings (each question must close on one):
    * SOLVENCY: the sum of all recorded claims against a contract == the NEAR it actually
      holds (`env::account_balance() + env::account_locked_balance()`, less reserved storage).
    * SETTLEMENT: value debited from an account == value actually delivered to it.
    * AUTHORISATION: every state change that moves value == one the entitled party requested,
      for that exact account and amount.
    * SCHEDULE: what can leave a lockup at time T == `get_balance() - get_locked_amount()`
      under the schedule it was initialised with.
    * THRESHOLD: confirmations counted for an executed request == `num_confirmations` distinct
      current members.
    * IDENTITY: an account trusted as a pool, whitelist, poll or lockup == the contract and
      arguments that trust was granted for.

    Each question must include:
    1. target struct/fn;
    2. attacker action (the exact call and arguments, deposit, transfer or deployed contract);
    3. preconditions (balances, epoch, existing accounts, whitelist and vote state);
    4. call sequence through the code;
    5. the binding that breaks, written as an equality;
    6. scoped impact and whose funds are affected;
    7. proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Method: struct_or_fn] Can an unprivileged ATTACKER_ACTION under PRECONDITIONS trigger CALL_SEQUENCE, breaking the binding BINDING_EQUALITY, causing scoped impact: SCOPE_IMPACT against PARTY? Proof idea: cargo test PARAMETERS asserting SOLVENCY, SETTLEMENT, AUTHORISATION, SCHEDULE, THRESHOLD, or IDENTITY.",
    ]
    """
    return prompt


def audit_format(security_question: str) -> str:
    """
    Generate a custody and authorization exploit-validation prompt for core-contracts.
    """

    prompt = f"""# SECURITY AUDIT PROMPT

## Question
{security_question}

## Rules
- Use existing repo context only. Analyze only this question and scoped impact.
- Attacker is unprivileged only: anyone who can send a NEAR transaction, attach a deposit, delegate to and withdraw from any staking pool, call any open method, transfer NEAR directly to a contract account, create their own lockup / staking pool / multisig through the public factories with chosen arguments, hold and move wNEAR, and deploy contracts they control and name as a whitelist, pool, poll or transfer target. They are NOT the NEAR Foundation or a whitelist `foundation_account_id`, not the owner of a victim's lockup or pool, not a multisig member, not a full-access key holder on a victim account, not a validator or node operator, and hold no victim key.
- Reject malicious validators, nodes or peers, key compromise, RPC or TLS interception, local or physical access, compromised dependencies and social engineering.
- OUT OF SCOPE, reject on sight: tests, fixtures and mocks (`**/tests/**`, `**/tests.rs`, `**/test_utils.rs`, `**/src/tests/**`, `#[cfg(test)]` blocks); build artefacts and tooling (`**/res/*.wasm`, `**/build.sh`, `scripts/**`, `.buildkite/**`), `*.toml`, `Cargo.lock`, `*.md`; gas or storage consumption, unbounded collections, denial of service, rate limiting and resource exhaustion; griefing with no attacker gain; anything requiring the foundation, a contract owner, a multisig member or a redeploy; nearcore / `near-sdk` / `near-contract-standards` defects with no path through this repository; best-practice notes; feature requests; theoretical findings with no demonstration.
- The impact must be one of: Critical - NEAR or wNEAR moved out of a pool, lockup, multisig or the wNEAR contract by a party not entitled to it, claims exceeding the NEAR actually held, locked or unvested tokens released early, an account whitelisted or a lockup deployed with parameters its rightful creator never chose, a multisig request executed below `num_confirmations` live members, or funds permanently frozen; High - rewards or owner fees attributed to the wrong party, funds frozen for at least one epoch but recoverable, or an accounting value diverging from reality where another party settles on it.
- Focus on real impact: NEAR leaving a contract account that the entitled party never authorised.

## Validate
- Write the binding the question claims is broken as an explicit equality between two named values BEFORE tracing any code.
- Trace the exact reachable path from the attacker's call, attached deposit, bare transfer or deployed callee, and record every read and write of: `last_total_balance`, `total_staked_balance`, `total_stake_shares`, `account.unstaked` / `stake_shares` / `unstaked_available_epoch_height`, `deposit_amount`, `termination_withdrawn_tokens`, `lockup_information`, `vesting_information`, `ft.total_supply` and the account rows, plus every `Promise` scheduled and every `assert_self()` callback that writes state from a promise result.
- Evaluate both sides of the equality before and after. If they still match, output no vulnerability.
- Check whether `assert_owner`, `assert_called_by_foundation`, `assert_self()`, `is_promise_success()`, `assert_one_yocto()`, `assert_transfers_enabled`, `assert_staking_pool_is_idle`, `assert_no_termination`, `assert_valid_request`, `assert_self_request`, `assert_one_action_only`, `internal_ping`'s balance assert, the U256 rounding pair or `is_valid_account_id` already prevents the divergence.
- State what the attacker gains or destroys per attempt and whether it is repeatable across accounts, epochs or contracts.
- Require exact file/fn support and a reproducible `cargo test` proof (`testing_env!` unit test, `near-sdk-sim` / `RuntimeStandalone`, or `near-workspaces`), with no mainnet.

## Output
If valid, output exactly:

### Title
[Bug statement] - ([File: file_path])

### Summary
[2-3 sentences]

### Finding Description
[The broken binding as an equality, the code path, root cause, the attacker's exact call or deployed contract, exploit flow, and why existing guards fail]

### Impact Explanation
[What is moved, released early, frozen or mis-credited, whose funds, repeatability, blast radius, matching severity category]

### Likelihood Explanation
[Preconditions, required balances and accounts, attacker cost, feasibility, repeatability]

### Recommendation
[Specific fix]

### Proof of Concept
[cargo test plan with the exact assertions on both sides of the binding]

If invalid, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict bounty-style validation prompt for core-contracts claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim.
- Check SECURITY.md and Researcher.Md for scope, exclusions, and valid impact classes.
- Do not create a new vulnerability if the submitted claim is weak or invalid.
- Do not upgrade severity unless the provided evidence proves the higher impact.
- A binding claim is only valid if the report states the broken equality between two named values and shows both sides concretely. Reject prose-only claims.
- Reject anything requiring the NEAR Foundation or a whitelist `foundation_account_id`, a staking pool or lockup owner, a multisig member, a full-access key on a victim account, a redeploy, a victim key, a malicious validator, node or peer, RPC or TLS interception, local or physical access, a compromised dependency, or social engineering.
- OUT OF SCOPE, reject on sight: tests, fixtures and mocks (`**/tests/**`, `**/tests.rs`, `**/test_utils.rs`, `**/src/tests/**`, `#[cfg(test)]` blocks); build artefacts and tooling (`**/res/*.wasm`, `**/build.sh`, `scripts/**`, `.buildkite/**`), `*.toml`, `Cargo.lock`, `*.md`; gas or storage consumption, unbounded collections, denial of service, rate limiting and resource exhaustion; griefing with no attacker gain; nearcore / `near-sdk` / `near-contract-standards` defects with no path through this repository; best-practice notes; feature requests; theoretical findings with no demonstration.
- The impact must be one of: Critical - NEAR or wNEAR moved out of a pool, lockup, multisig or the wNEAR contract by a party not entitled to it, claims exceeding the NEAR actually held, locked or unvested tokens released early, an account whitelisted or a lockup deployed with parameters its rightful creator never chose, a multisig request executed below `num_confirmations` live members, or funds permanently frozen; High - rewards or owner fees attributed to the wrong party, funds frozen for at least one epoch but recoverable, or an accounting value diverging from reality where another party settles on it.
- Reject claims that depend on a deployment ignoring the documented initialization, or that only harm the attacker's own funds.
- Reject if the bug was already fixed, publicly disclosed, or covered by an existing advisory or CHANGELOG entry for a supported version.
- Reject a divergence with no solvency, settlement, authorisation, schedule, threshold or identity boundary crossed.
- A valid report must be triggerable by an unprivileged attacker against the deployed contracts as they stand in this repository.
- A PoC is mandatory. Prefer #NoVulnerability over speculative reports.

## Required Validation Checks
All must pass:
1. Exact in-scope file, struct/fn, and line references.
2. The binding written explicitly as an equality, with both sides shown before and after.
3. Clear root cause: which unchecked caller, which hand-maintained balance field, which rounding direction, which fire-and-forget `Promise`, which attacker-supplied factory argument, which promise result trusted by an `assert_self()` callback.
4. Reachable exploit path: preconditions -> attacker call, deposit, transfer or deployed callee -> call sequence -> observed divergence.
5. `assert_owner`, `assert_called_by_foundation`, `assert_self()`, `is_promise_success()`, `assert_one_yocto()`, `assert_transfers_enabled`, `assert_staking_pool_is_idle`, `assert_valid_request`, `assert_self_request`, `assert_one_action_only`, `internal_ping`'s balance assert and the U256 rounding pair reviewed and shown insufficient.
6. Impact stated concretely: how much NEAR or wNEAR moves, whose, and whether it repeats.
7. Reproducible proof: `cargo test` (`testing_env!`, `near-sdk-sim` / `RuntimeStandalone`, or `near-workspaces`) with the asserted values, no mainnet.

## Silent Triage Questions
Before output, internally answer:
- What exactly is the equality, and does it actually fail?
- Can an ordinary delegator, lockup owner created through the public factory, wNEAR holder or plain caller trigger it with no role and no victim key?
- Is the flaw in this repository's in-scope code, not in a dependency, a test, or a careless deployment?
- What NEAR moves, or whose funds freeze, and is it repeatable?
- Would a NEAR triager accept the exploit path?
- What exact test would prove it?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the broken binding and impact]

## Finding Description
[Exact code path, the equality, root cause, exploit flow, and why existing guards fail]

## Impact Explanation
[What is moved, released early, frozen or mis-credited, affected party, repeatability, severity category]

## Likelihood Explanation
[Attacker capability, preconditions, configuration, cost, feasibility]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or cargo test plan with concrete assertions]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt


def scan_format(report: str) -> str:
    """
    Generate a short cross-project analog scan prompt for core-contracts.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Rules
- Use in-scope repository context only (`staking-pool/src/**`, `lockup/src/**`, `lockup-factory/src/**`, `staking-pool-factory/src/**`, `multisig-factory/src/**`, `whitelist/src/**`, `voting/src/**`, `multisig/src/**`, `multisig2/src/**`, `w-near/src/**`, `state-manipulation/src/**`), excluding tests, fixtures, build artefacts and tooling. Do not ask for code or claim missing files.
- Use the external report only as a bug-class hint, not as proof.
- Keep only unprivileged-attacker analogs that break a custody binding: recorded claims versus the NEAR actually held, value debited versus value delivered, shares charged versus shares redeemed, a lockup's releasable amount versus its schedule, confirmations counted versus live members, an account trusted as a pool or whitelist versus the code and arguments that trust was granted for.
- OUT OF SCOPE, reject on sight: tests, fixtures and mocks; build artefacts and tooling (`**/res/*.wasm`, `**/build.sh`, `scripts/**`, `.buildkite/**`), `*.toml`, `Cargo.lock`, `*.md`; gas or storage consumption, denial of service, rate limiting and resource exhaustion; griefing with no attacker gain; anything requiring the foundation, a contract owner, a multisig member, a victim key, a redeploy, a malicious validator or node, RPC interception, local access or social engineering; nearcore / `near-sdk` / `near-contract-standards` defects with no path through this repository; best-practice notes; feature requests; theoretical findings.
- The impact must be one of: Critical - NEAR or wNEAR moved by a party not entitled to it, claims exceeding assets held, locked or unvested tokens released early, a wrongly whitelisted or wrongly parameterised deployment, a multisig request executed below threshold, or funds permanently frozen; High - rewards or fees mis-attributed, funds frozen for at least one epoch, or an accounting value diverging from reality where another party settles on it.
- Reject analogs that depend on a deployment ignoring the documented initialization, and analogs with no solvency, settlement, authorisation, schedule, threshold or identity boundary crossed.

## Validate
- Map the bug class to the strongest reachable path in this repository and state the binding it would break as an equality.
- Evaluate both sides before and after the attacker's call, deposit, transfer or deployed callee.
- Prove root cause with exact file/fn support.
- Accept only concrete NEAR loss, an unauthorised move, an early release, an insolvent ledger, or frozen funds.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt
