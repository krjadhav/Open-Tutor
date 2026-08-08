"""Services layer: the deterministic grader and the LLM diagnosis.

The split between these two modules is the single most important architectural decision in the
product, and it is empirically justified rather than stylistic (learning-design.md sections 14
and 16):

  - `grading` decides correctness. It is a CAS, it is instant, and it is the only thing allowed
    to say "you are wrong".
  - `diagnose` explains a wrong answer and routes blame to a graph node. It is never asked
    whether the student is right, because the worst failure ever observed in the experiments was
    the model inventing an error inside a correct-but-unsimplified answer.

Call order is always: grade first, diagnose only when grading returned correct=False.
"""
