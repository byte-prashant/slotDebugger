                ┌──────────────────────┐
                │      START           │
                └─────────┬────────────┘
                          │
                          ▼
                ┌──────────────────────┐
                │   Load State         │
                │ (.think_state.json)  │
                └─────────┬────────────┘
                          │
                          ▼
        ┌────────────────────────────────────┐
        │  Is Plan Set? (--setPlan)          │
        └─────────┬───────────────┬──────────┘
                  │YES            │NO
                  ▼               ▼
      ┌──────────────────┐     ┌──────────────────────┐
      │ Set Plan         │     │ Get Current Step     │
      │ Reset index = 0  │     │ (state.plan[index])  │
      └─────────┬────────┘     └─────────┬────────────┘
                │                        │
                ▼                        ▼
        ┌────────────────────────────────────┐
        │ User provides Thought input       │
        │ (--thought, --thoughtNumber...)   │
        └─────────┬──────────────────────────┘
                  │
                  ▼
        ┌────────────────────────────────────┐
        │ Attach Plan Step to Thought       │
        │ thought.planStep = currentStep    │
        └─────────┬──────────────────────────┘
                  │
                  ▼
        ┌────────────────────────────────────┐
        │ Check Type of Thought             │
        └───────┬───────────┬───────────────┘
                │           │
         ┌──────▼─────┐ ┌───▼───────────┐
         │ Revision   │ │ Branch        │
         │ (isRevision│ │ (branchFrom)  │
         └──────┬─────┘ └────┬──────────┘
                │             │
                ▼             ▼
      ┌────────────────┐  ┌────────────────────┐
      │ Append to main │  │ Append to branch   │
      │ history        │  │ state.branches[id] │
      └──────┬─────────┘  └─────────┬──────────┘
             │                      │
             └──────────┬───────────┘
                        ▼
             ┌──────────────────────┐
             │ Save State           │
             └─────────┬────────────┘
                       │
                       ▼
        ┌────────────────────────────────────┐
        │ Output Thought (formatted)        │
        │ + show current plan step          │
        └─────────┬──────────────────────────┘
                  │
                  ▼
        ┌────────────────────────────────────┐
        │ Advance Step? (--nextStep)         │
        └─────────┬───────────────┬──────────┘
                  │YES            │NO
                  ▼               ▼
     ┌────────────────────┐   ┌──────────────────┐
     │ currentStepIndex++ │   │ Stay on step     │
     └─────────┬──────────┘   └─────────┬────────┘
               │                        │
               └──────────┬─────────────┘
                          ▼
                ┌──────────────────────┐
                │        END           │
                └──────────────────────┘

1) A thought is generated , could be random, to do analysis
2) Plan is to set direction, not random
IN reference to slot - Plan is which components rtp is not matching
-- find sub componnent
-- then check for reel, weights, winning calcualtion etc
NOTE: Rewrite in clean way