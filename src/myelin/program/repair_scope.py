"""A repair may expand a failed UI operation, while unaffected steps stay byte-equivalent."""

from myelin.program.bindings import BindingError
from myelin.schema import UiStep


def validate_scope(program, prior, scope):
    if len(scope) != 1:
        raise BindingError("this repair adapter requires one localized failure")
    index = next(i for i, s in enumerate(prior.steps) if s.id == scope[0])
    prefix, suffix = prior.steps[:index], prior.steps[index + 1 :]
    if program.steps[:index] != prefix or (suffix and program.steps[-len(suffix) :] != suffix):
        raise BindingError("repair changed unaffected prefix or suffix")
    middle = program.steps[
        index : len(program.steps) - len(suffix) if suffix else len(program.steps)
    ]
    if not middle or sum(s.id == scope[0] for s in middle) != 1:
        raise BindingError("repair lost the failed step identity")
    if isinstance(prior.steps[index], UiStep):
        if not all(isinstance(s, UiStep) for s in middle):
            raise BindingError("UI repair must preserve the genuine UI dependency")
    elif len(middle) != 1:
        raise BindingError("HTTP repair must remain localized")
    if program.final_post != prior.final_post:
        raise BindingError("repair changed final assertions")
