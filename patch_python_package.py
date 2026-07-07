import ast
import glob
import os

import toml

patchright_version = os.environ.get('patchright_release') or os.environ.get('playwright_version')
patchright_driver_version = os.environ.get('patchright_driver_version') or patchright_version

# The patched Playwright driver (Patchright stealth + our networkidle captcha
# blacklist, both baked in at the driver level via ts-morph) is published on our
# nodejs fork's GitHub releases. The python wheel must download THAT driver, not
# build a vanilla one from npm.
DRIVER_REPO = "bugbasesecurity/patchright"

def patch_file(file_path: str, patched_tree: ast.AST) -> None:
    with open(file_path, "w") as f:
        f.write(ast.unparse(ast.fix_missing_locations(patched_tree)))

def patch_driver_version_file() -> None:
    # Playwright-Python 1.61+ reads driver_version from a DRIVER_VERSION file
    # instead of a string constant in setup.py. Pin it to the patchright driver
    # version so the download URL resolves to our published release asset.
    driver_version_file = "playwright-python/DRIVER_VERSION"
    if os.path.exists(driver_version_file):
        with open(driver_version_file, "w") as f:
            f.write(f"{patchright_driver_version}\n")

def patch_ensure_driver_bundle(node: ast.FunctionDef) -> None:
    # Playwright-Python 1.61+ assembles the driver bundle from the vanilla
    # playwright-core npm package via ensure_driver_bundle(). Replace its body so
    # it downloads our patched driver ZIP from the fork's GitHub releases instead.
    if node.name != "ensure_driver_bundle":
        return
    node.body = ast.parse(f'''\
destination_path = f"driver/playwright-{{driver_version}}-{{zip_name}}.zip"
if os.path.exists(destination_path):
    return
os.makedirs("driver", exist_ok=True)
url = f"https://github.com/{DRIVER_REPO}/releases/download/v{{driver_version}}/playwright-{{driver_version}}-{{zip_name}}.zip"
subprocess.check_call(["curl", "-L", "--fail", "-o", destination_path, url])
if not os.path.exists(destination_path):
    raise RuntimeError(f"Driver bundle {{destination_path}} was not downloaded.")
''').body

# Adding _repo_version.py (Might not be intended but fixes the build)
with open("playwright-python/playwright/_repo_version.py", "w") as f:
    f.write(f"version = '{patchright_version}'")

# Patching pyproject.toml
with open("playwright-python/pyproject.toml", "r") as f:
    pyproject_source = toml.load(f)

    pyproject_source["project"]["name"] = "patchright"
    pyproject_source["project"]["description"] = "Undetected Python version of the Playwright testing and automation library."
    pyproject_source["project"]["authors"] = [{'name': 'Microsoft Corporation, patched by github.com/Kaliiiiiiiiii-Vinyzu/'}]

    pyproject_source["project"]["urls"]["homepage"] = "https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python"
    pyproject_source["project"]["urls"]["Release notes"] = "https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python/releases"
    pyproject_source["project"]["urls"]["Bug Reports"] = "https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python/issues"
    pyproject_source["project"]["urls"]["homeSource Codepage"] = "https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python"

    del pyproject_source["project"]["scripts"]["playwright"]
    pyproject_source["project"]["scripts"]["patchright"] = "patchright.__main__:main"
    pyproject_source["project"]["entry-points"]["pyinstaller40"]["hook-dirs"] = "patchright._impl.__pyinstaller:get_hook_dirs"

    pyproject_source["tool"]["setuptools"]["packages"] = ['patchright', 'patchright.async_api', 'patchright.sync_api', 'patchright._impl', 'patchright._impl.__pyinstaller']
    pyproject_source["tool"]["setuptools_scm"] = {'version_file': 'patchright/_repo_version.py'}

    with open("playwright-python/pyproject.toml", "w") as f:
        toml.dump(pyproject_source, f)

# Pin DRIVER_VERSION (Playwright-Python 1.61+ file-based driver version)
patch_driver_version_file()

# Patching setup.py
with open("playwright-python/setup.py") as f:
    setup_source = f.read()
    setup_tree = ast.parse(setup_source)

    for node in ast.walk(setup_tree):
        # Redirect the 1.61+ driver bundle download to our patched release asset
        if isinstance(node, ast.FunctionDef):
            patch_ensure_driver_bundle(node)

        # Modify driver_version (pre-1.61 string-constant form; no-op on 1.61+)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id == "driver_version" and node.value.value.startswith("1."):
                node.value.value = patchright_driver_version

        # Modify url (pre-1.61 cdn form; no-op on 1.61+)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id == "url" and node.value.value in ("https://cdn.playwright.dev/builds/driver/", "https://playwright.azureedge.net/builds/driver/"):
                node.value = ast.JoinedStr(
                    values=[
                        ast.Constant(value=f'https://github.com/{DRIVER_REPO}/releases/download/v'),
                        ast.FormattedValue(value=ast.Name(id='driver_version', ctx=ast.Load()), conversion=-1),
                        ast.Constant(value='/')
                    ]
                )

        # Modify Curl Call
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and len(node.args) >= 1 and isinstance(node.args[0], ast.List) and len(node.args[0].elts) == 4:
            if node.func.value.id == "subprocess" and node.func.attr == "check_call" and node.args[0].elts[0].value == "curl":
                node.args[0].elts.insert(1, ast.Constant(value="-L"))

        # Modify Shutil Call
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and len(node.args) >= 1 and isinstance(node.args[0], ast.Constant):
            if node.func.value.id == "shutil" and node.func.attr == "rmtree" and node.args[0].value == "playwright.egg-info":
                node.args[0].value = "patchright.egg-info"

        # Modify Os Makedirs Call
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and len(node.args) >= 1 and isinstance(node.args[0], ast.Constant):
            if node.func.value.id == "os" and node.func.attr == "makedirs" and node.args[0].value == "playwright/driver":
                node.args[0].value = "patchright/driver"

        # Modify Zip Write Call
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and len(node.args) >= 2 and isinstance(node.args[1], ast.JoinedStr):
            if node.func.value.id == "zip" and node.func.attr == "write" and node.args[1].values[0].value == "playwright/driver/":
                node.args[1].values[0].value = "patchright/driver/"

        # Modify Zip Writestr Call
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and len(node.args) >= 1 and isinstance(node.args[0], ast.Constant):
            if node.func.value.id == "zip" and node.func.attr == "writestr" and node.args[0].value == "playwright/driver/README.md":
                node.args[0].value = "patchright/driver/README.md"

        # Modify Extractall Call
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            if node.func.id == "extractall" and node.args[1].value == "playwright/driver":
                node.args[1].value = "patchright/driver"

        # Modify Setup Call
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "setup":
                node.keywords.append(ast.keyword(
                    arg="version",
                    value=ast.Constant(value=patchright_version)
                ))

    patch_file("playwright-python/setup.py", setup_tree)

# Patching playwright/_impl/__pyinstaller/hook-playwright.async_api.py
with open("playwright-python/playwright/_impl/__pyinstaller/hook-playwright.async_api.py") as f:
    async_api_source = f.read()
    async_api_tree = ast.parse(async_api_source)

    for node in ast.walk(async_api_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1 and isinstance(node.args[0], ast.Constant):
            if node.func.id == "collect_data_files" and node.args[0].value == "playwright":
                node.args[0].value = "patchright"

    patch_file("playwright-python/playwright/_impl/__pyinstaller/hook-playwright.async_api.py", async_api_tree)

# Patching playwright/_impl/__pyinstaller/hook-playwright.sync_api.py
with open("playwright-python/playwright/_impl/__pyinstaller/hook-playwright.sync_api.py") as f:
    async_api_source = f.read()
    async_api_tree = ast.parse(async_api_source)

    for node in ast.walk(async_api_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1 and isinstance(node.args[0], ast.Constant):
            if node.func.id == "collect_data_files" and node.args[0].value == "playwright":
                node.args[0].value = "patchright"

    patch_file("playwright-python/playwright/_impl/__pyinstaller/hook-playwright.sync_api.py", async_api_tree)

# Patching playwright/_impl/_browser.py
with open("playwright-python/playwright/_impl/_browser.py") as f:
    browser_source = f.read()
    browser_tree = ast.parse(browser_source)

    for node in ast.walk(browser_tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in ["new_context", "new_page"]:
            node.args.kwonlyargs.append(ast.arg(
                arg="focusControl",
                annotation=ast.Subscript(
                    value=ast.Name(id="Optional", ctx=ast.Load()),
                    slice=ast.Name(id="bool", ctx=ast.Load()),
                    ctx=ast.Load(),
                ),
            ))
            node.args.kw_defaults.append(ast.Constant(value=None))
    patch_file("playwright-python/playwright/_impl/_browser.py", browser_tree)

# Patching playwright/_impl/_browser_type.py
with open("playwright-python/playwright/_impl/_browser_type.py") as f:
    browser_type_source = f.read()
    browser_type_tree = ast.parse(browser_type_source)

    for node in ast.walk(browser_type_tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "launch_persistent_context":
            node.args.kwonlyargs.append(ast.arg(
                arg="focusControl",
                annotation=ast.Subscript(
                    value=ast.Name(id="Optional", ctx=ast.Load()),
                    slice=ast.Name(id="bool", ctx=ast.Load()),
                    ctx=ast.Load(),
                ),
            ))
            node.args.kw_defaults.append(ast.Constant(value=None))
    patch_file("playwright-python/playwright/_impl/_browser_type.py", browser_type_tree)

# Patching playwright/_impl/_driver.py
with open("playwright-python/playwright/_impl/_driver.py") as f:
    driver_source = f.read()
    driver_tree = ast.parse(driver_source)

    for node in ast.walk(driver_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and len(node.args) >= 1 and isinstance(node.args[0], ast.Name):
            if node.func.value.id == "inspect" and node.func.attr == "getfile" and node.args[0].id == "playwright":
                node.args[0].id = "patchright"

    patch_file("playwright-python/playwright/_impl/_driver.py", driver_tree)

# Patching playwright/_impl/_connection.py
with open("playwright-python/playwright/_impl/_connection.py") as f:
    connection_source = f.read()
    connection_source_tree = ast.parse(connection_source)

    for node in ast.walk(connection_source_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) >= 1 and isinstance(node.args[0], ast.Attribute):
            if node.func.id == "Path" and node.args[0].value.id == "playwright":
                node.args[0].value.id = "patchright"

        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Attribute) and isinstance(node.value.value.value, ast.Name):
            if node.value.value.value.id == "playwright" and node.value.value.attr == "_impl" and node.value.attr == "_impl_to_api_mapping":
                node.value.value.value.id = "patchright"

    patch_file("playwright-python/playwright/_impl/_connection.py", connection_source_tree)

# Patching playwright/_impl/_js_handle.py
with open("playwright-python/playwright/_impl/_js_handle.py") as f:
    js_handle_source = f.read()
    js_handle_tree = ast.parse(js_handle_source)

    for node in ast.walk(js_handle_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "add_source_url_to_script":
            for function_node in node.body:
                if isinstance(function_node, ast.Return):
                    function_node.value = ast.Name(id="source", ctx=ast.Load())

        if isinstance(node, ast.AsyncFunctionDef) and node.name in ["evaluate", "evaluate_handle"]:
            node.args.kwonlyargs.append(ast.arg(
                arg="isolatedContext",
                annotation=ast.Subscript(
                    value=ast.Name(id="Optional", ctx=ast.Load()),
                    slice=ast.Name(id="bool", ctx=ast.Load()),
                    ctx=ast.Load(),
                ),
            ))
            node.args.kw_defaults.append(ast.Constant(value=True))

            for subnode in ast.walk(node):
                if isinstance(subnode, ast.Return) and isinstance(subnode.value, ast.Call):
                    if subnode.value.args and isinstance(subnode.value.args[0], ast.Await):
                        inner_call = subnode.value.args[0].value
                        if isinstance(inner_call, ast.Call) and inner_call.func.attr == "send":
                            for i, arg in enumerate(inner_call.args):
                                if isinstance(arg, ast.Call) and arg.func.id == "dict":
                                    arg.keywords.append(ast.keyword(
                                        arg="isolatedContext",
                                        value=ast.Name(id="isolatedContext", ctx=ast.Load())
                                    ))

    patch_file("playwright-python/playwright/_impl/_js_handle.py", js_handle_tree)

# Patching playwright/_impl/_frame.py
with open("playwright-python/playwright/_impl/_frame.py") as f:
    frame_source = f.read()
    frame_tree = ast.parse(frame_source)

    for node in ast.walk(frame_tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in ["evaluate", "evaluate_handle", "eval_on_selector_all"]:
            node.args.kwonlyargs.append(ast.arg(
                arg="isolatedContext",
                annotation=ast.Subscript(
                    value=ast.Name(id="Optional", ctx=ast.Load()),
                    slice=ast.Name(id="bool", ctx=ast.Load()),
                    ctx=ast.Load(),
                ),
            ))
            node.args.kw_defaults.append(ast.Constant(value=True))

            for subnode in ast.walk(node):
                if isinstance(subnode, ast.Return) and isinstance(subnode.value, ast.Call):
                    if subnode.value.args and isinstance(subnode.value.args[0], ast.Await):
                        inner_call = subnode.value.args[0].value
                        if isinstance(inner_call, ast.Call) and inner_call.func.attr == "send":
                            for i, arg in enumerate(inner_call.args):
                                if isinstance(arg, ast.Call) and arg.func.id == "dict":
                                    arg.keywords.append(ast.keyword(
                                        arg="isolatedContext",
                                        value=ast.Name(id="isolatedContext", ctx=ast.Load())
                                    ))

    patch_file("playwright-python/playwright/_impl/_frame.py", frame_tree)

# Patching playwright/_impl/_locator.py
with open("playwright-python/playwright/_impl/_locator.py") as f:
    frame_source = f.read()
    frame_tree = ast.parse(frame_source)

    for node in ast.walk(frame_tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in ["evaluate", "evaluate_handle", "evaluate_all"]:
            node.args.kwonlyargs.append(ast.arg(
                arg="isolatedContext",
                annotation=ast.Subscript(
                    value=ast.Name(id="Optional", ctx=ast.Load()),
                    slice=ast.Name(id="bool", ctx=ast.Load()),
                    ctx=ast.Load(),
                ),
            ))
            node.args.kw_defaults.append(ast.Constant(value=True))

            for subnode in ast.walk(node):
                if isinstance(subnode, ast.Return) and isinstance(subnode.value, ast.Await):
                    call_expr = subnode.value.value
                    if isinstance(call_expr, ast.Call):
                        if node.name in ["evaluate", "evaluate_handle"] and isinstance(call_expr.func, ast.Attribute):
                            if call_expr.func.attr == "_with_element":
                                if call_expr.args and isinstance(call_expr.args[0], ast.Lambda):
                                    lambda_func = call_expr.args[0].body
                                    if isinstance(lambda_func, ast.Call) and isinstance(lambda_func.func, ast.Attribute):
                                        if lambda_func.func.attr == node.name:
                                            lambda_func.keywords.append(ast.keyword(
                                                arg="isolatedContext",
                                                value=ast.Name(id="isolatedContext", ctx=ast.Load())
                                            ))
                            elif call_expr.func.attr == "eval_on_selector_all":
                                call_expr.keywords.append(ast.keyword(
                                    arg="isolatedContext",
                                    value=ast.Name(id="isolatedContext", ctx=ast.Load())
                                ))


    patch_file("playwright-python/playwright/_impl/_locator.py", frame_tree)

# Patching playwright/_impl/_browser_context.py
with open("playwright-python/playwright/_impl/_browser_context.py") as f:
    browser_context_source = f.read()
    browser_context_tree = ast.parse(browser_context_source)

    for node in ast.walk(browser_context_tree):
        if isinstance(node, ast.ClassDef) and node.name == "BrowserContext":
            for class_node in node.body:
                if isinstance(class_node, ast.AsyncFunctionDef) and class_node.name == "add_init_script":
                    class_node.body.insert(0, ast.parse("await self.install_inject_route()"))
                elif isinstance(class_node, ast.AsyncFunctionDef) and class_node.name == "expose_binding":
                    class_node.body.insert(0, ast.parse("await self.install_inject_route()"))

            node.body.append(
                ast.Assign(
                    targets=[ast.Name(id='route_injecting', ctx=ast.Store())],
                    value=ast.Constant(value=False))
            )

            node.body.append(
                ast.parse("""\
async def install_inject_route(self) -> None:
    from patchright._impl._impl_to_api_mapping import ImplToApiMapping
    mapping = ImplToApiMapping()

    async def route_handler(route: Route) -> None:
            try:
                if route.request.resource_type == "document" and route.request.url.startswith("http"):
                    protocol = route.request.url.split(":")[0]
                    await route.fallback(url=f"{protocol}://patchright-init-script-inject.internal/")
                else:
                    await route.fallback()
            except:
                await route.fallback()

    if not self.route_injecting:
        if self._connection._is_sync:
            self._routes.insert(
                0,
                RouteHandler(
                    self._options.get("baseURL"),
                    "**/*",
                    mapping.wrap_handler(route_handler),
                    False,
                    None,
                ),
            )
            await self._update_interception_patterns()
        else:
            await self.route("**/*", mapping.wrap_handler(route_handler))
        self.route_injecting = True""").body[0])

    patch_file("playwright-python/playwright/_impl/_browser_context.py", browser_context_tree)

# Patching playwright/_impl/_page.py
with open("playwright-python/playwright/_impl/_page.py") as f:
    page_source = f.read()
    page_tree = ast.parse(page_source)

    for node in ast.walk(page_tree):
        if isinstance(node, ast.ClassDef) and node.name == "Page":
            for class_node in node.body:
                if isinstance(class_node, ast.AsyncFunctionDef) and class_node.name == "add_init_script":
                    class_node.body.insert(0, ast.parse("await self.install_inject_route()"))
                elif isinstance(class_node, ast.AsyncFunctionDef) and class_node.name == "expose_binding":
                    class_node.body.insert(0, ast.parse("await self.install_inject_route()"))

            node.body.append(
                ast.Assign(
                    targets=[ast.Name(id='route_injecting', ctx=ast.Store())],
                    value=ast.Constant(value=False))
            )

            node.body.append(
                ast.parse("""\
async def install_inject_route(self) -> None:
    from patchright._impl._impl_to_api_mapping import ImplToApiMapping
    mapping = ImplToApiMapping()
    
    async def route_handler(route: Route) -> None:
            try:
                if route.request.resource_type == "document" and route.request.url.startswith("http"):
                    protocol = route.request.url.split(":")[0]
                    await route.fallback(url=f"{protocol}://patchright-init-script-inject.internal/")
                else:
                    await route.fallback()
            except:
                await route.fallback()

    if not self.route_injecting and not self.context.route_injecting:
        if self._connection._is_sync:
            self._routes.insert(
                0,
                RouteHandler(
                    self._browser_context._options.get("baseURL"),
                    "**/*",
                    mapping.wrap_handler(route_handler),
                    False,
                    None,
                ),
            )
            await self._update_interception_patterns()
        else:
            await self.route("**/*", mapping.wrap_handler(route_handler))
        self.route_injecting = True""").body[0])

        if isinstance(node, ast.AsyncFunctionDef) and node.name in ["evaluate", "evaluate_handle"]:
            node.args.kwonlyargs.append(ast.arg(
                arg="isolatedContext",
                annotation=ast.Subscript(
                    value=ast.Name(id="Optional", ctx=ast.Load()),
                    slice=ast.Name(id="bool", ctx=ast.Load()),
                    ctx=ast.Load(),
                ),
            ))
            node.args.kw_defaults.append(ast.Constant(value=True))

            if "evaluateExpression" in ast.unparse(node.body[0]):
                for subnode in ast.walk(node):
                    if isinstance(subnode, ast.Return) and isinstance(subnode.value, ast.Call):
                        if subnode.value.args and isinstance(subnode.value.args[0], ast.Await):
                            inner_call = subnode.value.args[0].value
                            if isinstance(inner_call, ast.Call) and inner_call.func.attr == "send":
                                for i, arg in enumerate(inner_call.args):
                                    if isinstance(arg, ast.Call) and arg.func.id == "dict":
                                        arg.keywords.append(ast.keyword(
                                            arg="isolatedContext",
                                            value=ast.Name(id="isolatedContext", ctx=ast.Load())
                                        ))
            elif "_main_frame" in ast.unparse(node.body[0]):
                for subnode in ast.walk(node):
                    if isinstance(subnode, ast.Return) and isinstance(subnode.value, ast.Await) and isinstance(subnode.value.value, ast.Call):
                        subnode.value.value.keywords.append(ast.keyword(
                            arg="isolatedContext",
                            value=ast.Name(id="isolatedContext", ctx=ast.Load())
                        ))


    patch_file("playwright-python/playwright/_impl/_page.py", page_tree)

# Patching playwright/_impl/_clock.py
with open("playwright-python/playwright/_impl/_clock.py") as f:
    clock_source = f.read()
    clock_tree = ast.parse(clock_source)

    for node in ast.walk(clock_tree):
        if isinstance(node, ast.ClassDef) and node.name == "Clock":
            for class_node in node.body:
                if isinstance(class_node, ast.AsyncFunctionDef) and class_node.name == "install":
                    class_node.body.insert(0, ast.parse("await self._browser_context.install_inject_route()"))

    patch_file("playwright-python/playwright/_impl/_clock.py", clock_tree)

# Patching playwright/_impl/_tracing.py
with open("playwright-python/playwright/_impl/_tracing.py") as f:
    tracing_source = f.read()
    tracing_tree = ast.parse(tracing_source)

    for node in ast.walk(tracing_tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "start":
            node.body.insert(0, ast.parse("await self._parent.install_inject_route()"))

    patch_file("playwright-python/playwright/_impl/_tracing.py", tracing_tree)

# Patching playwright/async_api/_generated.py
with open("playwright-python/playwright/async_api/_generated.py") as f:
    async_generated_source = f.read()
    async_generated_tree = ast.parse(async_generated_source)

    for class_node in ast.walk(async_generated_tree):
        if isinstance(class_node, ast.ClassDef) and class_node.name in ["Page", "Frame", "Worker", "Locator", "JSHandle"]:
            for node in class_node.body:
                if isinstance(node, ast.AsyncFunctionDef) and (node.name in ["evaluate", "evaluate_handle"] or (class_node.name == "Locator" and node.name == "evaluate_all")):  # , "evaluate_all"
                    new_arg = ast.arg(arg="isolated_context", annotation=ast.Subscript(
                        value=ast.Name(id="typing.Optional", ctx=ast.Load()),
                        slice=ast.Name(id="bool", ctx=ast.Load()),
                        ctx=ast.Load(),
                    ))
                    # Append the argument to kwonlyargs
                    node.args.kwonlyargs.append(new_arg)
                    node.args.kw_defaults.append(ast.Constant(value=True))

                    # Modify the inner function call inside return statement
                    for subnode in ast.walk(node):
                        if isinstance(subnode, ast.Call) and isinstance(subnode.func, ast.Attribute) and subnode.func.attr == node.name:
                            subnode.keywords.append(
                                ast.keyword(arg="isolatedContext",
                                            value=ast.Name(id="isolated_context",
                                                           ctx=ast.Load())
                                            )
                            )
        if isinstance(class_node, ast.ClassDef) and class_node.name in ["Browser", "BrowserType"]:
            for node in class_node.body:
                if isinstance(node, ast.AsyncFunctionDef) and (node.name in ["new_context", "new_page", "launch_persistent_context"]):
                    new_arg = ast.arg(arg="focus_control", annotation=ast.Subscript(
                        value=ast.Name(id="typing.Optional", ctx=ast.Load()),
                        slice=ast.Name(id="bool", ctx=ast.Load()),
                        ctx=ast.Load(),
                    ))
                    # Append the argument to kwonlyargs
                    node.args.kwonlyargs.append(new_arg)
                    node.args.kw_defaults.append(ast.Constant(value=None))

                    for subnode in ast.walk(node):
                        if isinstance(subnode, ast.Call) and isinstance(subnode.func, ast.Attribute) and subnode.func.attr == node.name:
                            subnode.keywords.append(
                                ast.keyword(arg="focusControl", value=ast.Name(id="focus_control", ctx=ast.Load()))
                            )

    patch_file("playwright-python/playwright/async_api/_generated.py", async_generated_tree)

# Patching playwright/sync_api/_generated.py
with open("playwright-python/playwright/sync_api/_generated.py") as f:
    async_generated_source = f.read()
    async_generated_tree = ast.parse(async_generated_source)

    for class_node in ast.walk(async_generated_tree):
        if isinstance(class_node, ast.ClassDef) and class_node.name in ["Page", "Frame", "Worker", "Locator", "JSHandle"]:
            for node in class_node.body:
                if isinstance(node, ast.FunctionDef) and (node.name in ["evaluate", "evaluate_handle"] or (class_node.name == "Locator" and node.name == "evaluate_all")): # , "evaluate_all"
                    new_arg = ast.arg(arg="isolated_context", annotation=ast.Subscript(
                        value=ast.Name(id="typing.Optional", ctx=ast.Load()),
                        slice=ast.Name(id="bool", ctx=ast.Load()),
                        ctx=ast.Load(),
                    ))
                    # Append the argument to kwonlyargs
                    node.args.kwonlyargs.append(new_arg)
                    node.args.kw_defaults.append(ast.Constant(value=True))

                    # Modify the inner function call inside return statement
                    for subnode in ast.walk(node):
                        if isinstance(subnode, ast.Call) and isinstance(subnode.func, ast.Attribute) and subnode.func.attr == node.name:
                            subnode.keywords.append(
                                ast.keyword(arg="isolatedContext",
                                            value=ast.Name(id="isolated_context",
                                                           ctx=ast.Load())
                                            )
                            )

        if isinstance(class_node, ast.ClassDef) and class_node.name in ["Browser", "BrowserType", "BrowserContext"]:
            for node in class_node.body:
                if isinstance(node, ast.FunctionDef) and (node.name in ["new_context", "new_page", "launch_persistent_context"]):
                    new_arg = ast.arg(arg="focus_control", annotation=ast.Subscript(
                        value=ast.Name(id="typing.Optional", ctx=ast.Load()),
                        slice=ast.Name(id="bool", ctx=ast.Load()),
                        ctx=ast.Load(),
                    ))
                    # Append the argument to kwonlyargs
                    node.args.kwonlyargs.append(new_arg)
                    node.args.kw_defaults.append(ast.Constant(value=None))


                    if class_node.name != "BrowserContext" and node.name != "new_page":
                        for subnode in ast.walk(node):
                            if isinstance(subnode, ast.Call) and isinstance(subnode.func, ast.Attribute) and subnode.func.attr == node.name:
                                subnode.keywords.append(
                                    ast.keyword(arg="focusControl", value=ast.Name(id="focus_control", ctx=ast.Load()))
                                )

    patch_file("playwright-python/playwright/sync_api/_generated.py", async_generated_tree)

# Patching Imports of every python file under the playwright-python/playwright directory
for python_file in glob.glob("playwright-python/playwright/**.py") + glob.glob("playwright-python/playwright/**/**.py"):
    with open(python_file) as f:
        file_source = f.read()
        file_tree = ast.parse(file_source)
        renamed_attributes = []

        for node in ast.walk(file_tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("playwright"):
                        if "__init__" in python_file:
                            renamed_attributes.append(alias.name)
                        alias.name = alias.name.replace("playwright", "patchright", 1)
            if isinstance(node, ast.ImportFrom) and node.module.startswith("playwright"):
                node.module = node.module.replace("playwright", "patchright", 1)
            if renamed_attributes and isinstance(node, ast.Attribute):
                unparsed_attribute = ast.unparse(node.value)
                if unparsed_attribute in renamed_attributes:
                    node.value = ast.parse(unparsed_attribute.replace("playwright", "patchright", 1)).body[0].value

        patch_file(python_file, file_tree)

# NOTE: the networkidle captcha blacklist is applied at the DRIVER level (in the
# bugbasesecurity/patchright nodejs fork via ts-morph framesPatch.ts) and is
# already baked into the driver ZIP downloaded above. The old python-side
# frames.js patch was removed: modern drivers bundle everything into
# coreBundle.js (no standalone server/frames.js), so it patched nothing.

# Rename the Package Folder to Patchright
os.rename("playwright-python/playwright", "playwright-python/patchright")

# Write the Projects README to the README which is used in the release
with open("README.md", 'r', encoding='utf-8') as src:
    with open("playwright-python/README.md", 'w', encoding='utf-8') as dst:
        # Read from the source readme and write to the destination readme
        dst.write(src.read())
