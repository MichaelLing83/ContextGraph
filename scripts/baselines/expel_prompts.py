"""ExpeL-style prompt templates for SWE-bench rule extraction.

These prompts adapt ExpeL's critique-based insight extraction to the
software engineering domain (debugging, code navigation, testing).
"""

# System instruction for the critique LLM call when comparing success vs failure
SYSTEM_CRITIQUE_COMPARE = """\
You are an expert software engineering mentor analyzing debugging trajectories.
You will be given two previous attempts at fixing a code issue in a repository:
one SUCCESSFUL attempt and one FAILED attempt for the same or similar task.

The failed attempt may have failed because:
- The agent made incorrect edits that broke other functionality
- The agent got stuck in a loop (repeating the same actions)
- The agent ran out of steps without finding the root cause
- The agent misidentified the source of the bug

Your job is to extract GENERAL lessons (not specific to any file/variable name)
that would help a coding agent avoid the same class of mistakes in the future.
"""

# System instruction when analyzing only successful trajectories
SYSTEM_CRITIQUE_ALL_SUCCESS = """\
You are an expert software engineering mentor analyzing successful debugging trajectories.
You will be given several successful attempts at fixing code issues across different repositories.

Your job is to identify COMMON PATTERNS in how the successful agent approached problems,
and extract GENERAL, REUSABLE rules that would help a coding agent solve new problems
in any repository.

Focus on:
- How the agent navigated unfamiliar codebases
- How the agent reproduced and diagnosed errors
- How the agent validated fixes
- Strategic decisions that led to efficient solutions
"""

# System instruction when analyzing only failed trajectories
SYSTEM_CRITIQUE_ALL_FAIL = """\
You are an expert software engineering mentor analyzing failed debugging trajectories.
You will be given several failed attempts at fixing code issues.

Your job is to identify COMMON MISTAKES and ANTI-PATTERNS, and extract GENERAL rules
that would help a coding agent avoid these pitfalls in the future.

Focus on:
- Common reasoning errors
- Inefficient exploration strategies
- Failure to validate assumptions
- Getting stuck in loops or dead ends
"""

# Human message template for comparing success vs failure (with existing rules)
HUMAN_CRITIQUE_COMPARE = """\
Here are two previous attempts to compare and critique:

TASK DESCRIPTION:
{task_description}

SUCCESSFUL ATTEMPT (summarized):
{success_summary}

FAILED ATTEMPT (summarized):
{fail_summary}

Here are the EXISTING RULES:
{existing_rules}

By examining and contrasting the successful and failed attempts, and considering \
the list of existing rules, perform the following operations: ADD, EDIT, REMOVE, \
or AGREE so that the resulting rule list contains GENERAL and HIGH-LEVEL insights \
about software debugging that can help avoid similar failures on DIFFERENT tasks \
in the future.

{operations_format}

{suffix}
"""

# Human message template for analyzing only successes (with existing rules)
HUMAN_CRITIQUE_ALL_SUCCESS = """\
Here are successful debugging attempts across different repositories:

{success_summaries}

Here are the EXISTING RULES:
{existing_rules}

By examining the successful attempts, and the list of existing rules, perform the \
following operations: ADD, EDIT, REMOVE, or AGREE so that the resulting rule list \
contains GENERAL and HIGH-LEVEL insights about effective software debugging \
strategies. Focus on tips that help an agent perform better reasoning and actions. \

{operations_format}

{suffix}
"""

# Human message template for analyzing only failures (with existing rules)
HUMAN_CRITIQUE_ALL_FAIL = """\
Here are failed debugging attempts for the same or similar tasks:

TASK: {task_description}

FAILED ATTEMPTS:
{fail_summaries}

Here are the EXISTING RULES:
{existing_rules}

By examining the failed attempts and the existing rules, perform the following \
operations: ADD, EDIT, REMOVE, or AGREE so that the resulting rule list helps \
avoid these common failure patterns on DIFFERENT tasks in the future.

{operations_format}

{suffix}
"""

# Format for the operations instructions (appended to human messages)
OPERATIONS_FORMAT = """\
<OPERATION> <RULE NUMBER>: <RULE>

The available operations are:
- AGREE (if an existing rule is strongly relevant and confirmed by these examples)
- REMOVE (if an existing rule is contradicted, duplicated, or unhelpful)
- EDIT (if an existing rule can be improved or made more general — rewrite it)
- ADD (add a new rule that is DIFFERENT from existing rules and broadly applicable)

Format:
AGREE <EXISTING RULE NUMBER>: <EXISTING RULE>
REMOVE <EXISTING RULE NUMBER>: <EXISTING RULE>
EDIT <EXISTING RULE NUMBER>: <NEW IMPROVED RULE>
ADD <NEW RULE NUMBER>: <NEW RULE>

Do not reference specific repositories, files, or variable names in rules — \
all rules must be GENERALLY APPLICABLE to any codebase. Each rule should be \
concise (1-2 sentences) and actionable. Do at most 4 operations. Each existing \
rule can receive at most 1 operation."""

# Suffix when the rule list is full (>= max_rules)
SUFFIX_FULL = """\
The rule list is near capacity. Focus on REMOVING weak or redundant rules first. \
Only ADD a new rule if it is VERY insightful and clearly different from all \
existing rules.

Below are the operations you perform on the EXISTING RULES:
"""

# Suffix when the rule list has room
SUFFIX_NOT_FULL = """\
Below are the operations you perform on the EXISTING RULES:
"""

# Template for injecting rules into SWE-agent system prompt
RULE_INJECTION_TEMPLATE = """\
The following are general debugging and code navigation rules learned from \
analyzing many past software engineering tasks. These rules are listed in \
decreasing order of confidence. Follow them as guidelines when approaching \
the problem:

{rules}

Remember: these are general heuristics. Apply judgment — if a rule conflicts \
with clear evidence in the current task, trust the evidence."""

# Template for summarizing a trajectory for the critique prompt
TRAJECTORY_SUMMARY_TEMPLATE = """\
Repository: {repo}
Problem: {problem_statement}
Outcome: {outcome}
Steps taken: {n_steps}
Key actions:
{action_summary}
"""
