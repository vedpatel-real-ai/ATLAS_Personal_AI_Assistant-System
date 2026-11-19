# Why the router includes function signatures

A request to open an application exposed a mismatch between the router and the executor. The model returned a parameter named `app`, while `open_app` takes `app_name`. Python rejected the unexpected keyword. The deterministic VS Code and Spotify patterns supplied the correct parameter, so those requests succeeded.

The router now includes each registered function’s parameter names in the prompt and shows a worked example. Before dispatch, the executor checks the signature and normalizes known aliases. For a single leftover parameter it can also map to a single unfilled argument. This handles common naming differences. The model can still choose the wrong tool or value. [Router prompt](../router.py) · [Executor](../main.py)

The router tests check the signatures sent to the model. The normalization tests exercise the `app` to `app_name` correction and other argument shapes without launching desktop applications. [Router tests](../tests/test_router.py) · [Parameter tests](../tests/test_normalize_params.py)
