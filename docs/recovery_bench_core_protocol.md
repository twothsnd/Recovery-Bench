# Recovery-Bench Core Protocol

## 1. Primary Goal

Recovery-Bench compares agent performance under the same allowance of `k` attempts, but with two different attempt protocols:

- **Retry@k**: each attempt is a fresh official benchmark run from the clean initial state.
- **Recovery@k**: after a failed attempt, the next attempt continues from the real environment state left by the previous failed attempt.

The core question is:

> Given the same number of attempts, how different is an agent's clean-state retry ability from its ability to recover from its own stateful failures?

## 2. Benchmark Invariance

Recovery-Bench must not change the original benchmark semantics.

The following should remain identical to the official benchmark:

- task definition;
- initial state;
- action space;
- default configuration;
- official evaluator or scorer;
- success and failure criteria;
- per-attempt budget definition.

Recovery-Bench only changes how multiple attempts are organized.

## 3. Attempt Branching Protocol

Evaluation is treated as a branching process over states.

Each attempt starts from a state node, runs under the official benchmark rules, and is then scored by the official evaluator.

After scoring:

- if the attempt succeeds, the branch terminates and is marked solved;
- if the attempt fails, the resulting post-attempt environment state becomes a failure-state node.

Retry and recovery branch differently:

- **Retry branch**: returns to the clean root state and starts a new official attempt.
- **Recovery branch**: starts from the failure-state node produced by the previous failed attempt.

Conceptually:

```text
root clean state
  attempt 1
    ├── success -> solved
    └── failure state S1
          ├── retry -> root clean state, new independent attempt
          └── recovery -> continue from S1
```

For sequential recovery:

```text
S0 clean state
  attempt A1
    └── failure -> S1
          attempt A2
            └── failure -> S2
                  attempt A3
                    ...
```

## 4. Failure-State Consistency

Recovery must continue from the real state caused by the agent's own failed attempt.

The recovery start state must be exactly consistent with the environment state at the end of the failed attempt. It must not be:

- reset to the initial state;
- rolled back;
- manually repaired;
- approximated by an imperfect replay;
- modified by non-official intervention.

The protocol does not prescribe a single engineering mechanism. Checkpointing, snapshotting, cloning, copy-on-write state, VM snapshots, or other mechanisms are all acceptable if they preserve exact failure-state consistency.

The key requirement is:

> The next recovery attempt must see the same state that the failed attempt actually left behind.

## 5. Evaluator Isolation

Each attempt must be scored before any retry or recovery continuation is selected.

If the official evaluator or scorer is read-only, it can be run directly.

If scoring mutates the environment, then scoring must be isolated from the recovery state. For example, scoring can run on a copied state, snapshot clone, or disposable branch. The scored result is used for control flow, but any evaluator-induced side effects must not contaminate the failure-state node used by recovery.

The scoring rule is:

> Score every attempt with the official evaluator, but preserve an uncontaminated failure state for recovery.

## 6. Memory Protocol

Retry and recovery differ in agent memory.

For **Retry@k**, each attempt is simply another official benchmark run. The new attempt should not receive the previous attempt's history.

For **Recovery@k**, the agent must retain complete memory across attempts, including:

- prior observations;
- prior actions;
- failed trajectories;
- intermediate conclusions;
- discovered environment information;
- previous mistakes and their consequences.

Recovery is not a memoryless rerun. It evaluates whether an agent can use its own failure history to continue solving the same task from the state it caused.

## 7. Budget Protocol

Each attempt receives the full official single-attempt budget.

The budget may include:

- environment steps;
- model tokens;
- wall-clock time;
- tool calls;
- benchmark-specific limits.

If the official benchmark gives 50 steps per attempt, then every retry or recovery attempt receives 50 steps. A previous failed attempt's consumed budget does not reduce the next attempt's budget.

The key rule is:

> State carries over in recovery; per-attempt budget refreshes.

## 8. No Artificial Rescue

Recovery must not introduce non-official assistance.

Apart from preserving the agent's own memory in the recovery protocol, the system must not provide:

- extra hints;
- extra tools;
- extra actions;
- hidden state edits;
- manual cleanup;
- rollback ability;
- modified success criteria;
- benchmark-specific rescue logic.

The agent must recover only through the official action space and official task rules.

## 9. Summary

Recovery-Bench keeps the original benchmark fixed and changes only the multi-attempt protocol.

Under **Retry@k**, each attempt returns to the clean root state and runs as an independent official attempt.

Under **Recovery@k**, failed attempts create failure-state nodes, and subsequent attempts continue from those exact states with complete agent memory and a refreshed official per-attempt budget.

In one sentence:

> Recovery-Bench compares clean-state retry against stateful recovery by organizing attempts as a state branching process, while preserving benchmark invariance, exact failure-state consistency, official scoring, full recovery memory, and full per-attempt budgets.
