"""Runs the offline learning pipeline: evaluate pending records, then learn if
enough new evaluated records are waiting. Called from the background thread
that records a closed incident (or by an endpoint), never from a request path.

One run at a time per process: a second caller returns "busy" immediately.
Not safe across multiple backend workers, consistent with the in-memory
LangGraph checkpointer the app already depends on."""

import logging
import threading

from ..config import settings
from .evaluator import LlmCall as EvalLlmCall
from .evaluator import evaluate_pending
from .learner import LlmCall as LearnLlmCall
from .learner import run_learning
from .models import GLOBAL_SCOPE, LearningRunResult
from .registry import PolicyRegistry
from .store import RecordStore

logger = logging.getLogger(__name__)

_run_lock = threading.Lock()


def run_learning_pipeline(
    *,
    store: RecordStore | None = None,
    registry: PolicyRegistry | None = None,
    eval_llm: EvalLlmCall | None = None,
    learn_llm: LearnLlmCall | None = None,
    scope: str = GLOBAL_SCOPE,
    force: bool = False,
) -> LearningRunResult:
    """Never raises."""
    if not settings.learning_enabled:
        return LearningRunResult(status="disabled")
    if not _run_lock.acquire(blocking=False):
        return LearningRunResult(status="busy")
    try:
        evaluated = evaluate_pending(store=store, llm_call=eval_llm, scope=scope)
        result = run_learning(
            store=store, registry=registry, llm_call=learn_llm, scope=scope, force=force
        )
        result.records_evaluated = len(evaluated)
        return result
    except Exception:
        logger.warning("learning pipeline failed", exc_info=True)
        return LearningRunResult(status="llm_failed")
    finally:
        _run_lock.release()


def is_running() -> bool:
    return _run_lock.locked()


def on_incident_closed(scope: str = GLOBAL_SCOPE) -> None:
    """Hook for the recorder thread: run the pipeline if it is due."""
    run_learning_pipeline(scope=scope)
