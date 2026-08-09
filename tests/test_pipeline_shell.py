"""
One guard: importing the pipeline must do nothing.

The pipeline used to run in full at import. Reaching the `import` statement
fetched the patch constants, looped every league fetching match details, and
rebuilt the CSV — all as a side effect. That is what made the transforms
untestable, and it is the acceptance criterion this file exists to hold:

    "Importing the pipeline module runs no network calls and writes no files."

This is the only test here that is not a data assertion, and it is deliberate.
The criterion is about what an import *doesn't* do, so no assertion on returned
data can express it. Everything else about the shell — fetching, checkpointing,
rate limiting — stays untested by design, and is verified by running it.

The stubs raise a BaseException rather than an Exception because the shell
catches Exception broadly: an ordinary failure would be swallowed by
`get_patch_map`, and the test would pass against exactly the code it exists to
catch. Blocking the network is also a safety catch — without it a regression
would make the *test suite* fetch every league for real. That is not
hypothetical; it happened while verifying this test against the pre-refactor
module, and took 133 seconds.
"""
import importlib
import os

import requests


class ImportTimeSideEffect(BaseException):
    """Raised by the stubs below so a swallowed failure cannot hide."""


def test_importing_the_pipeline_has_no_side_effects(monkeypatch):
    def refuse_network(*args, **kwargs):
        raise ImportTimeSideEffect("the pipeline called the API at import time")

    def refuse_disk(*args, **kwargs):
        raise ImportTimeSideEffect("the pipeline wrote to disk at import time")

    monkeypatch.setattr(requests, "get", refuse_network)
    monkeypatch.setattr(os, "makedirs", refuse_disk)
    monkeypatch.setattr("builtins.open", refuse_disk)

    import opendota_pipeline

    importlib.reload(opendota_pipeline)
