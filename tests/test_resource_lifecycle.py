import asyncio
import importlib
import sys
import types
import unittest


def _install_runtime_stubs() -> None:
    """Keep lifecycle tests runnable without downloading browser binaries."""
    quart = types.ModuleType("quart")

    class Quart:
        def __init__(self, *args, **kwargs):
            pass

        def before_serving(self, callback):
            return callback

        def route(self, *args, **kwargs):
            return lambda callback: callback

    quart.Quart = Quart
    quart.request = types.SimpleNamespace()
    quart.jsonify = lambda value: value
    sys.modules["quart"] = quart

    camoufox = types.ModuleType("camoufox")
    camoufox_async_api = types.ModuleType("camoufox.async_api")
    camoufox_async_api.AsyncCamoufox = object
    camoufox.async_api = camoufox_async_api
    sys.modules["camoufox"] = camoufox
    sys.modules["camoufox.async_api"] = camoufox_async_api

    rich = types.ModuleType("rich")
    rich.box = types.SimpleNamespace(ROUNDED=None)
    rich_console = types.ModuleType("rich.console")
    rich_panel = types.ModuleType("rich.panel")
    rich_text = types.ModuleType("rich.text")
    rich_align = types.ModuleType("rich.align")
    rich_console.Console = object
    rich_panel.Panel = object
    rich_text.Text = object
    rich_align.Align = object
    sys.modules["rich"] = rich
    sys.modules["rich.console"] = rich_console
    sys.modules["rich.panel"] = rich_panel
    sys.modules["rich.text"] = rich_text
    sys.modules["rich.align"] = rich_align


_RUNTIME_MODULE_NAMES = (
    "quart",
    "camoufox",
    "camoufox.async_api",
    "rich",
    "rich.console",
    "rich.panel",
    "rich.text",
    "rich.align",
)
_MISSING = object()
_original_modules = {
    name: sys.modules.get(name, _MISSING) for name in _RUNTIME_MODULE_NAMES
}
_original_api_solver = sys.modules.pop("api_solver", _MISSING)
_install_runtime_stubs()
api_solver = importlib.import_module("api_solver")
TurnstileAPIServer = api_solver.TurnstileAPIServer

for _name, _module in _original_modules.items():
    if _module is _MISSING:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _module
if _original_api_solver is _MISSING:
    sys.modules.pop("api_solver", None)
else:
    sys.modules["api_solver"] = _original_api_solver


class FakeBrowser:
    def __init__(self):
        self.close_calls = 0

    def is_connected(self):
        return True

    async def close(self):
        self.close_calls += 1


class FakeCamoufox:
    def __init__(self, outcomes):
        self._outcomes = iter(outcomes)
        self.close_calls = 0

    async def start(self):
        outcome = next(self._outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def aclose(self):
        self.close_calls += 1


class CancelledCloseCamoufox(FakeCamoufox):
    async def aclose(self):
        self.close_calls += 1
        raise asyncio.CancelledError()


class CancelledStopPlaywright:
    def __init__(self):
        self.stop_calls = 0

    async def stop(self):
        self.stop_calls += 1
        raise asyncio.CancelledError()


class FailingCloseContext:
    async def new_page(self):
        raise RuntimeError("page creation failed")

    async def close(self):
        raise RuntimeError("context close failed")


class CancelledCloseContext(FailingCloseContext):
    async def close(self):
        raise asyncio.CancelledError()


class BlockingCloseContext(FailingCloseContext):
    def __init__(self):
        self.close_started = asyncio.Event()
        self.allow_close = asyncio.Event()

    async def close(self):
        self.close_started.set()
        await self.allow_close.wait()


class ContextBrowser(FakeBrowser):
    def __init__(self, context):
        super().__init__()
        self.context = context

    async def new_context(self, **kwargs):
        return self.context


class CancelledCloseBrowser(ContextBrowser):
    async def close(self):
        self.close_calls += 1
        raise asyncio.CancelledError()


class DisconnectedBrowser(FakeBrowser):
    def is_connected(self):
        return False


def _bare_server():
    server = object.__new__(TurnstileAPIServer)
    server.debug = False
    server.browser_pool = asyncio.Queue()
    server._owned_browsers = []
    server._playwright = None
    server._camoufox = None
    server._pool_ready = False
    server._last_used = 0.0
    server._in_flight = 0
    server._lowmem = False
    server._lowmem_aggressive = False
    server._camoufox_prefs_applied = False
    server.useragent = None
    server.sec_ch_ua = None
    server.browser_name = None
    server.browser_version = None
    server.use_random_config = False
    server.headless = True
    server.proxy_support = False
    return server


class ResourceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_camoufox_start_failure_closes_started_browser(self):
        server = _bare_server()
        server.browser_type = "camoufox"
        server.thread_count = 2
        server._camoufox_prefs_applied = True
        first_browser = FakeBrowser()
        camoufox = FakeCamoufox([first_browser, RuntimeError("second start failed")])
        server._make_camoufox = lambda: camoufox

        with self.assertRaisesRegex(RuntimeError, "second start failed"):
            await server._initialize_browser()

        self.assertEqual(first_browser.close_calls, 1)
        self.assertEqual(camoufox.close_calls, 1)
        self.assertEqual(server.browser_pool.qsize(), 0)
        self.assertEqual(server._owned_browsers, [])
        self.assertIsNone(server._camoufox)
        self.assertFalse(server._pool_ready)

    async def test_partial_start_cleanup_survives_cancelled_camoufox_close(self):
        server = _bare_server()
        server.browser_type = "camoufox"
        server.thread_count = 2
        server._camoufox_prefs_applied = False
        first_browser = FakeBrowser()
        camoufox = CancelledCloseCamoufox(
            [first_browser, RuntimeError("second start failed")]
        )
        server._make_camoufox = lambda: camoufox

        with self.assertRaisesRegex(RuntimeError, "second start failed"):
            await server._initialize_browser()

        self.assertEqual(first_browser.close_calls, 1)
        self.assertEqual(camoufox.close_calls, 1)
        self.assertTrue(server.browser_pool.empty())
        self.assertEqual(server._owned_browsers, [])
        self.assertIsNone(server._camoufox)
        self.assertFalse(server._pool_ready)

    async def test_invalid_proxy_releases_acquired_browser(self):
        server = _bare_server()
        browser = FakeBrowser()
        slot = (1, browser, {})
        server._owned_browsers = [slot]
        server._pool_ready = True
        await server.browser_pool.put(slot)

        async def ensure_pool():
            return None

        server._ensure_pool = ensure_pool
        server._pick_proxy = lambda proxy: proxy
        saved_results = []

        async def save_result(task_id, task_type, data):
            saved_results.append((task_id, data))

        original_save_result = api_solver.save_result
        api_solver.save_result = save_result
        try:
            await server._solve_turnstile(
                task_id="task-1",
                url="https://example.test",
                sitekey="sitekey",
                proxy="broken@proxy",
            )
        finally:
            api_solver.save_result = original_save_result

        self.assertEqual(server.browser_pool.qsize(), 1)
        self.assertEqual(server._in_flight, 0)
        self.assertEqual(saved_results[-1][1]["value"], "CAPTCHA_FAIL")

    async def test_failed_context_close_closes_and_invalidates_browser_slot(self):
        server = _bare_server()
        context = FailingCloseContext()
        browser = ContextBrowser(context)
        slot = (1, browser, {})
        server._owned_browsers = [slot]
        server._pool_ready = True
        await server.browser_pool.put(slot)

        async def ensure_pool():
            return None

        server._ensure_pool = ensure_pool
        server._pick_proxy = lambda proxy: None
        saved_results = []

        async def save_result(task_id, task_type, data):
            saved_results.append((task_id, data))

        original_save_result = api_solver.save_result
        api_solver.save_result = save_result
        try:
            await server._solve_turnstile(
                task_id="task-2",
                url="https://example.test",
                sitekey="sitekey",
            )
        finally:
            api_solver.save_result = original_save_result

        self.assertEqual(browser.close_calls, 1)
        self.assertEqual(server._owned_browsers, [])
        self.assertFalse(server._pool_ready)
        self.assertEqual(server.browser_pool.qsize(), 1)
        self.assertEqual(server.browser_pool.get_nowait(), (None, None, None))
        self.assertEqual(saved_results[-1][1]["value"], "CAPTCHA_FAIL")

    async def test_pool_rebuild_marker_retries_browser_acquisition(self):
        server = _bare_server()
        replacement = FakeBrowser()
        await server.browser_pool.put((None, None, None))
        rebuilds = 0

        async def ensure_pool():
            nonlocal rebuilds
            if not server._pool_ready:
                rebuilds += 1
                server._pool_ready = True
                server._owned_browsers = [(1, replacement, {})]
                await server.browser_pool.put((1, replacement, {}))

        server._ensure_pool = ensure_pool

        index, browser, config = await server._acquire_browser()

        self.assertEqual(rebuilds, 1)
        self.assertEqual(index, 1)
        self.assertIs(browser, replacement)
        self.assertEqual(config, {})

    async def test_pool_rebuild_shutdown_preserves_in_flight_tasks(self):
        server = _bare_server()
        server._in_flight = 2

        await server._shutdown_browsers(reset_in_flight=False)

        self.assertEqual(server._in_flight, 2)

    async def test_forced_reclaim_old_task_cannot_decrement_new_task(self):
        server = _bare_server()
        old_task_token = server._begin_in_flight()

        await server._shutdown_browsers()
        new_task_token = server._begin_in_flight()
        server._finish_in_flight(old_task_token)

        self.assertEqual(server._in_flight, 1)

        server._finish_in_flight(new_task_token)
        self.assertEqual(server._in_flight, 0)

    async def test_old_solve_cannot_return_browser_into_rebuilt_pool(self):
        server = _bare_server()
        context = BlockingCloseContext()
        old_browser = ContextBrowser(context)
        old_slot = (1, old_browser, {})
        server._owned_browsers = [old_slot]
        server._pool_ready = True
        await server.browser_pool.put(old_slot)

        async def ensure_pool():
            return None

        async def save_result(task_id, task_type, data):
            return None

        server._ensure_pool = ensure_pool
        server._pick_proxy = lambda proxy: None
        original_save_result = api_solver.save_result
        api_solver.save_result = save_result
        try:
            solve_task = asyncio.create_task(
                server._solve_turnstile(
                    task_id="task-old-generation",
                    url="https://example.test",
                    sitekey="sitekey",
                )
            )
            await asyncio.wait_for(context.close_started.wait(), timeout=1.0)

            await server._shutdown_browsers()
            new_browser = FakeBrowser()
            new_slot = (1, new_browser, {})
            server._owned_browsers = [new_slot]
            server._pool_ready = True
            await server.browser_pool.put(new_slot)

            context.allow_close.set()
            await asyncio.wait_for(solve_task, timeout=1.0)
        finally:
            api_solver.save_result = original_save_result

        self.assertEqual(server._owned_browsers, [new_slot])
        self.assertEqual(server.browser_pool.qsize(), 1)
        self.assertEqual(server.browser_pool.get_nowait(), new_slot)

    async def test_disconnected_browser_slot_is_discarded_and_wakes_waiter(self):
        server = _bare_server()
        browser = DisconnectedBrowser()
        slot = (1, browser, {})
        server._owned_browsers = [slot]
        server._pool_ready = True
        await server.browser_pool.put(slot)

        async def ensure_pool():
            return None

        async def save_result(task_id, task_type, data):
            return None

        server._ensure_pool = ensure_pool
        force_kills = []

        async def force_kill(disconnected_browser, index=None):
            force_kills.append((disconnected_browser, index))

        server._force_kill_browser = force_kill
        original_save_result = api_solver.save_result
        api_solver.save_result = save_result
        try:
            await server._solve_turnstile(
                task_id="task-disconnected",
                url="https://example.test",
                sitekey="sitekey",
            )
        finally:
            api_solver.save_result = original_save_result

        self.assertEqual(server._owned_browsers, [])
        self.assertFalse(server._pool_ready)
        self.assertEqual(server._in_flight, 0)
        self.assertEqual(server.browser_pool.get_nowait(), (None, None, None))
        self.assertEqual(browser.close_calls, 1)
        self.assertEqual(force_kills, [(browser, 1)])

    async def test_idle_shutdown_handles_cancelled_browser_close(self):
        server = _bare_server()
        browser = CancelledCloseBrowser(FailingCloseContext())
        slot = (1, browser, {})
        server._owned_browsers = [slot]
        server._pool_ready = True
        await server.browser_pool.put(slot)
        force_kills = []

        async def force_kill(cancelled_browser, index=None):
            force_kills.append((cancelled_browser, index))

        server._force_kill_browser = force_kill

        await server._shutdown_browsers()

        self.assertEqual(browser.close_calls, 1)
        self.assertEqual(force_kills, [(browser, 1)])
        self.assertEqual(server._owned_browsers, [])
        self.assertFalse(server._pool_ready)
        self.assertTrue(server.browser_pool.empty())

    async def test_idle_shutdown_clears_drivers_when_playwright_stop_is_cancelled(self):
        server = _bare_server()
        playwright = CancelledStopPlaywright()
        camoufox = FakeCamoufox([])
        server._playwright = playwright
        server._camoufox = camoufox
        server._pool_ready = True

        await server._shutdown_browsers()

        self.assertEqual(playwright.stop_calls, 1)
        self.assertEqual(camoufox.close_calls, 1)
        self.assertIsNone(server._playwright)
        self.assertIsNone(server._camoufox)
        self.assertFalse(server._pool_ready)

    async def test_close_cancel_after_prior_task_cancel_is_resource_failure(self):
        server = _bare_server()

        async def close_after_prior_cancel():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                return await server._close_maybe_async(
                    CancelledCloseContext(), "close", label="context"
                )

        task = asyncio.create_task(close_after_prior_cancel())
        await asyncio.sleep(0)
        task.cancel()

        self.assertFalse(await asyncio.wait_for(task, timeout=1.0))

    async def test_cancelled_context_close_still_invalidates_browser_slot(self):
        server = _bare_server()
        browser = ContextBrowser(CancelledCloseContext())
        slot = (1, browser, {})
        server._owned_browsers = [slot]
        server._pool_ready = True
        await server.browser_pool.put(slot)

        async def ensure_pool():
            return None

        async def save_result(task_id, task_type, data):
            return None

        server._ensure_pool = ensure_pool
        server._pick_proxy = lambda proxy: None
        original_save_result = api_solver.save_result
        api_solver.save_result = save_result
        try:
            await server._solve_turnstile(
                task_id="task-3",
                url="https://example.test",
                sitekey="sitekey",
            )
        finally:
            api_solver.save_result = original_save_result

        self.assertEqual(browser.close_calls, 1)
        self.assertEqual(server._owned_browsers, [])
        self.assertFalse(server._pool_ready)
        self.assertEqual(server._in_flight, 0)
        self.assertEqual(server.browser_pool.get_nowait(), (None, None, None))

    async def test_cancelled_browser_close_still_invalidates_browser_slot(self):
        server = _bare_server()
        browser = CancelledCloseBrowser(FailingCloseContext())
        slot = (1, browser, {})
        server._owned_browsers = [slot]
        server._pool_ready = True
        await server.browser_pool.put(slot)

        async def ensure_pool():
            return None

        async def save_result(task_id, task_type, data):
            return None

        server._ensure_pool = ensure_pool
        server._pick_proxy = lambda proxy: None
        force_kills = []

        async def force_kill(cancelled_browser, index=None):
            force_kills.append((cancelled_browser, index))

        server._force_kill_browser = force_kill
        original_save_result = api_solver.save_result
        api_solver.save_result = save_result
        try:
            await server._solve_turnstile(
                task_id="task-4",
                url="https://example.test",
                sitekey="sitekey",
            )
        finally:
            api_solver.save_result = original_save_result

        self.assertEqual(browser.close_calls, 1)
        self.assertEqual(force_kills, [(browser, 1)])
        self.assertEqual(server._owned_browsers, [])
        self.assertFalse(server._pool_ready)
        self.assertEqual(server._in_flight, 0)
        self.assertEqual(server.browser_pool.get_nowait(), (None, None, None))

    async def test_external_cancel_finishes_cleanup_and_propagates(self):
        server = _bare_server()
        context = BlockingCloseContext()
        browser = ContextBrowser(context)
        slot = (1, browser, {})
        server._owned_browsers = [slot]
        server._pool_ready = True
        await server.browser_pool.put(slot)

        async def ensure_pool():
            return None

        async def save_result(task_id, task_type, data):
            return None

        server._ensure_pool = ensure_pool
        server._pick_proxy = lambda proxy: None
        original_save_result = api_solver.save_result
        api_solver.save_result = save_result
        try:
            solve_task = asyncio.create_task(
                server._solve_turnstile(
                    task_id="task-5",
                    url="https://example.test",
                    sitekey="sitekey",
                )
            )
            await asyncio.wait_for(context.close_started.wait(), timeout=1.0)
            solve_task.cancel()
            context.allow_close.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(solve_task, timeout=1.0)
        finally:
            api_solver.save_result = original_save_result

        self.assertEqual(server._in_flight, 0)
        self.assertTrue(server._pool_ready)
        self.assertEqual(server._owned_browsers, [slot])
        self.assertEqual(server.browser_pool.get_nowait(), slot)


if __name__ == "__main__":
    unittest.main()
